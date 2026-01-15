import os
import re
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime, timedelta
import concurrent.futures
import instaloader
import requests
import webbrowser
from collections import deque
from PIL import Image, ImageTk
import json
import socket
import subprocess
import shutil

APP_NAME = "RDE"
APP_AUTHOR = "Orangepeels24"
APP_DISCORD = "https://discord.gg/RnY4Xk29Nq"
APP_VERSION = "1.5.0 [Klondike] [UNSTABLE]"

RATE_LIMIT_REQUESTS = 5
RATE_LIMIT_WINDOW = 60
RETRY_DELAY = 3

HISTORY_FILE = "download_history.json"
QUEUE_FILE = "queue_backup.json"
STATS_FILE = "download_stats.json"

def create_loader():
    return instaloader.Instaloader(
        download_pictures=False,
        download_videos=True,
        download_video_thumbnails=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
        post_metadata_txt_pattern="",
        filename_pattern="{shortcode}",
        dirname_pattern=None
    )


class RateLimiter:
    def __init__(self, max_requests, time_window):
        self.max_requests = max_requests
        self.time_window = time_window
        self.requests = deque()
        self.lock = threading.Lock()
    
    def is_allowed(self):
        with self.lock:
            now = datetime.now()
            while self.requests and self.requests[0] < now - timedelta(seconds=self.time_window):
                self.requests.popleft()
            
            if len(self.requests) < self.max_requests:
                self.requests.append(now)
                return True
            return False
    
    def wait_if_needed(self):
        if not self.is_allowed():
            with self.lock:
                if self.requests:
                    oldest = self.requests[0]
                    wait_time = (oldest + timedelta(seconds=self.time_window) - datetime.now()).total_seconds()
                    if wait_time > 0:
                        return wait_time
        return 0


class HistoryManager:
    def __init__(self, filepath=HISTORY_FILE):
        self.filepath = filepath
        self.history = self.load()
    
    def load(self):
        try:
            if os.path.exists(self.filepath):
                with open(self.filepath, 'r') as f:
                    return json.load(f)
        except:
            pass
        return []
    
    def save(self):
        try:
            with open(self.filepath, 'w') as f:
                json.dump(self.history, f, indent=2)
        except Exception as e:
            print(f"Failed to save history: {e}")
    
    def add(self, shortcode, url, success, file_type="video"):
        self.history.append({
            "shortcode": shortcode,
            "url": url,
            "timestamp": datetime.now().isoformat(),
            "success": success,
            "type": file_type
        })
        self.save()
    
    def exists(self, shortcode):
        return any(h["shortcode"] == shortcode for h in self.history)
    
    def clear(self):
        self.history = []
        self.save()
    
    def export(self):
        if not self.history:
            return "No history"
        csv = "Shortcode,URL,Timestamp,Success,Type\n"
        for h in self.history:
            csv += f"{h['shortcode']},{h['url']},{h['timestamp']},{h['success']},{h['type']}\n"
        return csv


class StatsManager:
    def __init__(self, filepath=STATS_FILE):
        self.filepath = filepath
        self.stats = self.load()
    
    def load(self):
        try:
            if os.path.exists(self.filepath):
                with open(self.filepath, 'r') as f:
                    return json.load(f)
        except:
            pass
        return {
            "total_downloads": 0,
            "total_size_mb": 0,
            "total_time_hours": 0,
            "success_count": 0,
            "fail_count": 0,
            "avg_speed_mbps": 0
        }
    
    def save(self):
        try:
            with open(self.filepath, 'w') as f:
                json.dump(self.stats, f, indent=2)
        except Exception as e:
            print(f"Failed to save stats: {e}")
    
    def update(self, success, size_mb=0, time_seconds=0):
        self.stats["total_downloads"] += 1
        self.stats["total_size_mb"] += size_mb
        self.stats["total_time_hours"] += time_seconds / 3600
        if success:
            self.stats["success_count"] += 1
        else:
            self.stats["fail_count"] += 1
        
        if time_seconds > 0:
            speed = (size_mb * 8) / time_seconds
            self.stats["avg_speed_mbps"] = (self.stats["avg_speed_mbps"] + speed) / 2
        
        self.save()
    
    def get_summary(self):
        return f"Total: {self.stats['total_downloads']} | Success: {self.stats['success_count']} | Failed: {self.stats['fail_count']} | Data: {self.stats['total_size_mb']:.1f}MB"


def is_internet_connected():
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=2)
        return True
    except:
        return False

class ReelDownloader:
    def __init__(self, root):
        self.root = root
        self.root.title(f"{APP_NAME} v{APP_VERSION}")
        self.root.geometry("1100x800")
        self.root.minsize(900, 600)

        self.set_favicon()
        self.configure_theme()

        self.loader = create_loader()
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
        self.rate_limiter = RateLimiter(RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW)

        self.download_path = os.path.join(
            os.path.expanduser("~"),
            "Downloads",
            "InstagramReels"
        )
        os.makedirs(self.download_path, exist_ok=True)

        self.queue = []
        self.failed = []
        self.completed = 0
        self.stop_flag = False
        self.active_downloads = 0
        self.download_lock = threading.Lock()
        
        self.quality_var = tk.StringVar(value="high")
        self.download_images_var = tk.BooleanVar(value=False)
        self.download_thumbnails_var = tk.BooleanVar(value=False)
        self.extract_audio_var = tk.BooleanVar(value=False)
        self.compress_video_var = tk.BooleanVar(value=False)
        self.folder_org_var = tk.StringVar(value="flat")
        self.concurrent_limit_var = tk.IntVar(value=4)
        
        self.history = HistoryManager()
        self.stats = StatsManager()
        
        self.load_queue_backup()

        self.build_ui()

    def configure_theme(self):
        style = ttk.Style()
        style.theme_use("clam")
        
        bg_color = "#f0f0f0"
        fg_color = "#1a1a1a"
        accent_color = "#0066cc"
        accent_light = "#e6f0ff"
        
        style.configure("TFrame", background=bg_color)
        style.configure("TLabel", background=bg_color, foreground=fg_color)
        style.configure("TLabelframe", background=bg_color, foreground=fg_color)
        style.configure("TLabelframe.Label", background=bg_color, foreground=fg_color)
        style.configure("TButton", font=("Segoe UI", 10))
        style.map("TButton", background=[("active", accent_color)])
        
        self.root.configure(bg=bg_color)

    def set_favicon(self):
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            favicon_path = os.path.join(script_dir, "favicon.ico")

            if os.path.exists(favicon_path):
                self.root.iconbitmap(favicon_path)

            try:
                import ctypes
                myappid = f'{APP_AUTHOR}.{APP_VERSION}'
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
            except:
                pass
        except:
            pass

    def load_logo(self, frame, size=(80, 80)):
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            logo_path = os.path.join(script_dir, "assets/logo.png")
            
            if os.path.exists(logo_path):
                img = Image.open(logo_path)
                img.thumbnail(size, Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                logo_label = ttk.Label(frame, image=photo)
                logo_label.image = photo
                logo_label.pack(side=tk.LEFT, padx=10)
                return True
        except Exception as e:
            print(f"Could not load logo: {e}")
        return False

    def load_queue_backup(self):
        try:
            if os.path.exists(QUEUE_FILE):
                with open(QUEUE_FILE, 'r') as f:
                    data = json.load(f)
                    self.queue = data.get('queue', [])
                    if self.queue:
                        self.log(f"✓ Restored {len(self.queue)} items from previous session")
        except Exception as e:
            print(f"Failed to load queue backup: {e}")
    
    def save_queue_backup(self):
        try:
            with open(QUEUE_FILE, 'w') as f:
                json.dump({'queue': self.queue, 'timestamp': datetime.now().isoformat()}, f)
        except Exception as e:
            print(f"Failed to save queue backup: {e}")

    def build_ui(self):
        style = ttk.Style()
        style.theme_use("clam")

        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(main_frame)
        header.pack(fill=tk.X, padx=15, pady=15)

        self.load_logo(header, size=(80, 80))
        
        title_frame = ttk.Frame(header)
        title_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10)
        
        ttk.Label(title_frame, text=APP_NAME, font=("Segoe UI", 24, "bold")).pack(anchor="w")
        ttk.Label(title_frame, text=f"Version {APP_VERSION}", font=("Segoe UI", 9), foreground="gray").pack(anchor="w")

        ttk.Separator(main_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=15)

        content = ttk.Frame(main_frame, padding=15)
        content.pack(fill=tk.BOTH, expand=True)

        url_box = ttk.LabelFrame(content, text="📋 Add Instagram URLs", padding=10)
        url_box.pack(fill=tk.X, pady=10)

        self.url_text = tk.Text(url_box, height=4, wrap=tk.WORD)
        self.url_text.pack(fill=tk.X, pady=(0, 10))

        url_btns = ttk.Frame(url_box)
        url_btns.pack(fill=tk.X)

        ttk.Button(url_btns, text="➕ Add to Queue", command=self.add_urls).pack(side=tk.LEFT, padx=5)
        ttk.Button(url_btns, text="📋 Paste URLs", command=self.paste_urls).pack(side=tk.LEFT, padx=5)
        ttk.Button(url_btns, text="🗑️ Clear", command=lambda: self.url_text.delete("1.0", tk.END)).pack(side=tk.LEFT, padx=5)
        ttk.Button(url_btns, text="👤 User Batch", command=self.user_dialog).pack(side=tk.LEFT, padx=5)

        settings_frame = ttk.LabelFrame(content, text="⚙️ Settings & Options", padding=10)
        settings_frame.pack(fill=tk.X, pady=10)

        path_container = ttk.Frame(settings_frame)
        path_container.pack(fill=tk.X, pady=5)

        ttk.Label(path_container, text="Download Folder:", width=15).pack(side=tk.LEFT)
        self.path_var = tk.StringVar(value=self.download_path)
        ttk.Entry(path_container, textvariable=self.path_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(path_container, text="📁 Browse", command=self.browse).pack(side=tk.LEFT)

        options_container = ttk.Frame(settings_frame)
        options_container.pack(fill=tk.X, pady=5)

        ttk.Label(options_container, text="Quality:", width=10).pack(side=tk.LEFT)
        quality_combo = ttk.Combobox(options_container, textvariable=self.quality_var, 
                                     values=["high", "medium", "low"], state="readonly", width=10)
        quality_combo.pack(side=tk.LEFT, padx=5)

        ttk.Label(options_container, text="Organize:", width=10).pack(side=tk.LEFT)
        org_combo = ttk.Combobox(options_container, textvariable=self.folder_org_var, 
                                 values=["flat", "by-date", "by-user", "by-type"], state="readonly", width=10)
        org_combo.pack(side=tk.LEFT, padx=5)

        ttk.Label(options_container, text="Concurrent:", width=10).pack(side=tk.LEFT)
        concurrent_spin = ttk.Spinbox(options_container, from_=1, to=10, textvariable=self.concurrent_limit_var, width=8)
        concurrent_spin.pack(side=tk.LEFT, padx=5)

        options_container2 = ttk.Frame(settings_frame)
        options_container2.pack(fill=tk.X, pady=5)

        ttk.Checkbutton(options_container2, text="📸 Images", variable=self.download_images_var).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(options_container2, text="🖼️ Thumbnails", variable=self.download_thumbnails_var).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(options_container2, text="🎵 Extract Audio", variable=self.extract_audio_var).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(options_container2, text="🎬 Compress Video", variable=self.compress_video_var).pack(side=tk.LEFT, padx=5)

        ctrl_frame = ttk.Frame(content)
        ctrl_frame.pack(fill=tk.X, pady=10)

        self.start_btn = ttk.Button(ctrl_frame, text="▶️ Start Download", command=self.start)
        self.start_btn.pack(side=tk.LEFT, padx=5)

        self.stop_btn = ttk.Button(ctrl_frame, text="⏹️ Stop", command=self.stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5)

        ttk.Button(ctrl_frame, text="🔄 Retry Failed", command=self.retry_failed).pack(side=tk.LEFT, padx=5)
        ttk.Button(ctrl_frame, text="📊 Stats", command=self.show_stats).pack(side=tk.LEFT, padx=5)
        
        spacer = ttk.Frame(ctrl_frame)
        spacer.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(ctrl_frame, text="🔍 Filter Logs", command=self.filter_logs).pack(side=tk.LEFT, padx=5)
        ttk.Button(ctrl_frame, text="ℹ️ Credits", command=self.credits).pack(side=tk.RIGHT, padx=5)

        progress_frame = ttk.LabelFrame(content, text="📊 Progress", padding=10)
        progress_frame.pack(fill=tk.X, pady=10)

        self.progress = ttk.Progressbar(progress_frame, mode="determinate")
        self.progress.pack(fill=tk.X, pady=5)

        self.progress_label = ttk.Label(progress_frame, text="Ready", font=("Segoe UI", 10))
        self.progress_label.pack(anchor="w")

        log_box = ttk.LabelFrame(content, text="📝 Log", padding=10)
        log_box.pack(fill=tk.BOTH, expand=True, pady=10)

        self.log_text = tk.Text(log_box, height=8, wrap=tk.WORD, state="disabled")
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scroll = ttk.Scrollbar(log_box, command=self.log_text.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.log_text.config(yscrollcommand=scroll.set)

        self.log(f"✓ Ready! Using {self.concurrent_limit_var.get()} concurrent downloads")
        if self.queue:
            self.log(f"📥 Restored {len(self.queue)} items from previous session")

    def log(self, msg):
        self.log_text.config(state="normal")
        timestamp = datetime.now().strftime('%H:%M:%S')
        self.log_text.insert(
            tk.END,
            f"[{timestamp}] {msg}\n"
        )
        self.log_text.see(tk.END)
        self.log_text.config(state="disabled")
        self.root.update_idletasks()

    def add_urls(self):
        urls = [
            line.strip()
            for line in self.url_text.get("1.0", tk.END).splitlines()
            if "instagram.com" in line and line.strip()
        ]
        
        if not urls:
            messagebox.showwarning("Warning", "No valid Instagram URLs found")
            return
        
        self.queue.extend(urls)
        self.save_queue_backup()
        self.log(f"✓ Added {len(urls)} URL(s). Queue: {len(self.queue)}")
        self.url_text.delete("1.0", tk.END)

    def paste_urls(self):
        try:
            clipboard_text = self.root.clipboard_get()
            urls = re.findall(r'https://(?:www\.)?instagram\.com/[^/\s<>]+', clipboard_text)
            
            if urls:
                self.queue.extend(urls)
                self.save_queue_backup()
                self.log(f"📋 Pasted {len(urls)} URL(s) from clipboard")
            else:
                messagebox.showinfo("Info", "No Instagram URLs found in clipboard")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to paste: {e}")

    def start(self):
        if not self.queue:
            messagebox.showinfo("Info", "Queue is empty")
            return

        self.download_path = os.path.abspath(self.path_var.get())
        
        if not os.path.isdir(self.download_path):
            messagebox.showerror("Error", "Invalid download path")
            return
        
        if not is_internet_connected():
            messagebox.showerror("Error", "No internet connection detected")
            return
        
        try:
            os.makedirs(self.download_path, exist_ok=True)
        except Exception as e:
            messagebox.showerror("Error", f"Cannot create folder: {e}")
            return

        self.completed = 0
        self.progress["maximum"] = len(self.queue)
        self.progress["value"] = 0
        self.stop_flag = False

        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        
        max_parallel = min(self.concurrent_limit_var.get(), len(self.queue))
        self.log(f"⏱️ Starting with {max_parallel} parallel downloads...")
        self.save_queue_backup()

        for _ in range(max_parallel):
            self.next_download()

    def stop(self):
        self.stop_flag = True
        self.log("⏹️ Stopping downloads...")

    def retry_failed(self):
        if not self.failed:
            self.log("✓ No failed downloads to retry")
            return
        
        failed_count = len(self.failed)
        self.queue.extend(self.failed)
        self.failed.clear()
        self.log(f"🔄 Retrying {failed_count} failed downloads...")
        self.start()

    def next_download(self):
        with self.download_lock:
            if self.stop_flag or not self.queue:
                if self.active_downloads == 0:
                    self.finish()
                return
            
            url = self.queue.pop(0)
            self.active_downloads += 1
        
        if not is_internet_connected():
            self.log("🌐 No internet, pausing downloads...")
            with self.download_lock:
                self.active_downloads -= 1
                self.queue.insert(0, url)
            self.save_queue_backup()
            return
        
        future = self.executor.submit(self.download_reel_manual, url)
        future.add_done_callback(self.download_done)

    def download_done(self, future):
        try:
            ok, url, shortcode = future.result()
            if not ok:
                self.failed.append(url)
        except Exception as e:
            self.log(f"⚠️ Download error: {e}")

        with self.download_lock:
            self.active_downloads -= 1
            self.completed += 1
        
        self.progress["value"] = self.completed
        self.progress_label.config(text=f"{self.completed} / {self.progress['maximum']} completed")
        self.save_queue_backup()

        if self.queue:
            self.next_download()
        elif self.active_downloads == 0:
            self.finish()

    def finish(self):
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        
        summary = f"✓ Complete! {self.completed} success, {len(self.failed)} failed"
        self.log(summary)
        self.progress_label.config(text=summary)
        
        if os.path.exists(QUEUE_FILE):
            os.remove(QUEUE_FILE)
        
        if self.failed:
            messagebox.showinfo("Complete", f"Downloaded: {self.completed}\nFailed: {len(self.failed)}\n\nUse 'Retry Failed' to retry")
        else:
            messagebox.showinfo("Complete", f"✓ All {self.completed} files downloaded!")
        
        self.show_notification("Download Complete", f"Successfully downloaded {self.completed} items")

    def download_reel_manual(self, url):
        retry_count = 0
        max_retries = 3
        start_time = datetime.now()
        
        while retry_count < max_retries:
            try:
                wait_time = self.rate_limiter.wait_if_needed()
                if wait_time > 0:
                    self.log(f"⏳ Rate limited {wait_time:.1f}s...")
                    import time
                    time.sleep(wait_time)
                
                if not self.rate_limiter.is_allowed():
                    raise Exception("Rate limit exceeded")
                
                m = re.search(r"/(reel|p|s)/([^/]+|[^/?]+)", url)
                if not m:
                    self.log(f"❌ Invalid URL: {url}")
                    return False, url, "invalid"

                shortcode = m.group(2)
                
                if self.history.exists(shortcode):
                    self.log(f"⏭️ Already in history: {shortcode}")
                    return True, url, shortcode
                
                self.log(f"⬇️ Downloading {shortcode}...")
                
                post = instaloader.Post.from_shortcode(self.loader.context, shortcode)
                
                if not post.is_video and self.download_images_var.get():
                    result = self._download_image(post, shortcode)
                    if result:
                        self.history.add(shortcode, url, True, "image")
                        return True, url, shortcode
                    else:
                        return False, url, shortcode
                
                if post.is_video:
                    result = self._download_video(post, shortcode)
                    elapsed = (datetime.now() - start_time).total_seconds()
                    if result:
                        self.history.add(shortcode, url, True, "video")
                        return True, url, shortcode
                    else:
                        return False, url, shortcode
                else:
                    self.log(f"⚠️ Post is not a video: {shortcode}")
                    return False, url, shortcode

            except requests.exceptions.Timeout:
                retry_count += 1
                if retry_count < max_retries:
                    self.log(f"⏱️ Timeout, retrying {retry_count}/{max_retries}...")
                    import time
                    time.sleep(RETRY_DELAY * (2 ** (retry_count - 1)))
                else:
                    self.log(f"❌ Timeout (max retries)")
                    return False, url, shortcode
            
            except requests.exceptions.ConnectionError:
                retry_count += 1
                if retry_count < max_retries:
                    self.log(f"🌐 Connection error, retrying...")
                    import time
                    time.sleep(RETRY_DELAY * (2 ** (retry_count - 1)))
                else:
                    self.log(f"❌ Connection failed")
                    return False, url, shortcode
            
            except Exception as e:
                error_msg = str(e)
                if "429" in error_msg or "rate" in error_msg.lower():
                    self.log(f"⚠️ Rate limited by Instagram...")
                    import time
                    time.sleep(RETRY_DELAY * 3)
                    retry_count += 1
                else:
                    self.log(f"❌ Error: {e}")
                    return False, url, shortcode
        
        return False, url, shortcode

    def _download_video(self, post, shortcode):
        try:
            quality = self.quality_var.get()
            
            if not hasattr(post, 'video_url') or not post.video_url:
                self.log(f"❌ No video URL: {shortcode}")
                return False

            save_dir = self._get_organized_path(shortcode, "video")
            os.makedirs(save_dir, exist_ok=True)
            
            filename = os.path.join(save_dir, f"{shortcode}.mp4")

            if os.path.exists(filename):
                self.log(f"⏭️ Exists: {shortcode}.mp4")
                return True

            r = requests.get(post.video_url, stream=True, timeout=30)
            r.raise_for_status()

            temp_filename = filename + ".tmp"
            downloaded_size = 0
            with open(temp_filename, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if self.stop_flag:
                        os.remove(temp_filename)
                        return False
                    if chunk:
                        f.write(chunk)
                        downloaded_size += len(chunk)
            
            if os.path.exists(filename):
                os.remove(filename)
            os.rename(temp_filename, filename)

            size_mb = downloaded_size / (1024 * 1024)
            self.log(f"✓ Video: {shortcode}.mp4 ({size_mb:.1f}MB)")
            
            if self.compress_video_var.get():
                self._compress_video(filename, shortcode)
            
            if self.extract_audio_var.get():
                self._extract_audio(filename, shortcode)
            
            if self.download_thumbnails_var.get():
                self._download_thumbnail(post, shortcode, save_dir)
            
            self.stats.update(True, size_mb, 10)
            return True

        except Exception as e:
            self.log(f"❌ Video error: {e}")
            self.stats.update(False, 0, 0)
            return False

    def _download_image(self, post, shortcode):
        try:
            save_dir = self._get_organized_path(shortcode, "image")
            os.makedirs(save_dir, exist_ok=True)
            
            downloaded_count = 0
            
            if post.is_carousel:
                for idx, item in enumerate(post.get_sidecar_nodes(), 1):
                    if not item.is_video:
                        try:
                            img_url = item.display_url
                            img_filename = os.path.join(save_dir, f"{shortcode}_{idx:02d}.jpg")
                            
                            if os.path.exists(img_filename):
                                continue
                            
                            r = requests.get(img_url, timeout=30)
                            r.raise_for_status()
                            
                            with open(img_filename, "wb") as f:
                                f.write(r.content)
                            downloaded_count += 1
                        except Exception as e:
                            self.log(f"⚠️ Image {idx} failed: {e}")
            else:
                try:
                    img_url = post.display_url
                    img_filename = os.path.join(save_dir, f"{shortcode}.jpg")
                    
                    if not os.path.exists(img_filename):
                        r = requests.get(img_url, timeout=30)
                        r.raise_for_status()
                        
                        with open(img_filename, "wb") as f:
                            f.write(r.content)
                        downloaded_count = 1
                except Exception as e:
                    self.log(f"⚠️ Image failed: {e}")
            
            if downloaded_count > 0:
                self.log(f"✓ Images: {downloaded_count} from {shortcode}")
                self.stats.update(True, 0, 5)
                return True
            else:
                self.log(f"⚠️ No new images: {shortcode}")
                return True

        except Exception as e:
            self.log(f"❌ Image error: {e}")
            self.stats.update(False, 0, 0)
            return False

    def _download_thumbnail(self, post, shortcode, save_dir):
        try:
            if hasattr(post, 'thumbnail_url') and post.thumbnail_url:
                thumb_filename = os.path.join(save_dir, f"{shortcode}_thumb.jpg")
                if not os.path.exists(thumb_filename):
                    r = requests.get(post.thumbnail_url, timeout=30)
                    r.raise_for_status()
                    with open(thumb_filename, "wb") as f:
                        f.write(r.content)
                    self.log(f"🖼️ Thumbnail saved")
        except Exception as e:
            self.log(f"⚠️ Thumbnail failed: {e}")

    def _compress_video(self, filename, shortcode):
        try:
            ffmpeg_path = shutil.which("ffmpeg")
            if not ffmpeg_path:
                self.log(f"⚠️ FFmpeg not found (install for compression)")
                return
            
            output = filename.replace(".mp4", "_compressed.mp4")
            cmd = [ffmpeg_path, "-i", filename, "-crf", "28", "-preset", "fast", output]
            
            result = subprocess.run(cmd, capture_output=True, timeout=600)
            if result.returncode == 0:
                orig_size = os.path.getsize(filename) / (1024 * 1024)
                comp_size = os.path.getsize(output) / (1024 * 1024)
                saved = orig_size - comp_size
                self.log(f"🎬 Compressed: {orig_size:.1f}MB → {comp_size:.1f}MB (saved {saved:.1f}MB)")
                os.remove(filename)
                os.rename(output, filename)
            else:
                self.log(f"⚠️ Compression failed")
                if os.path.exists(output):
                    os.remove(output)
        except Exception as e:
            self.log(f"⚠️ Compress error: {e}")

    def _extract_audio(self, video_filename, shortcode):
        try:
            ffmpeg_path = shutil.which("ffmpeg")
            if not ffmpeg_path:
                self.log(f"⚠️ FFmpeg not found (install for audio extraction)")
                return
            
            audio_filename = video_filename.replace(".mp4", ".mp3")
            cmd = [ffmpeg_path, "-i", video_filename, "-q:a", "0", "-map", "a", audio_filename]
            
            result = subprocess.run(cmd, capture_output=True, timeout=300)
            if result.returncode == 0:
                size_mb = os.path.getsize(audio_filename) / (1024 * 1024)
                self.log(f"🎵 Audio extracted: {shortcode}.mp3 ({size_mb:.1f}MB)")
            else:
                self.log(f"⚠️ Audio extraction failed")
                if os.path.exists(audio_filename):
                    os.remove(audio_filename)
        except Exception as e:
            self.log(f"⚠️ Audio error: {e}")

    def _get_organized_path(self, shortcode, media_type):
        org = self.folder_org_var.get()
        base = self.download_path
        
        if org == "by-date":
            date_folder = datetime.now().strftime("%Y/%m/%d")
            return os.path.join(base, date_folder)
        elif org == "by-type":
            return os.path.join(base, media_type)
        elif org == "by-user":
            return os.path.join(base, "posts")
        else:
            return base

    def user_dialog(self):
        win = tk.Toplevel(self.root)
        win.title("Batch Download User Reels")
        win.geometry("500x400")
        win.resizable(False, False)

        frame = ttk.Frame(win, padding=15)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="Username or Profile URL", font=("Segoe UI", 11)).pack(anchor="w", pady=(0, 5))
        user_entry = ttk.Entry(frame, width=40, font=("Segoe UI", 10))
        user_entry.pack(pady=(0, 15), fill=tk.X)
        user_entry.focus()

        ttk.Label(frame, text="Max reels to download", font=("Segoe UI", 11)).pack(anchor="w", pady=(0, 5))
        count_var = tk.StringVar(value="10")
        ttk.Entry(frame, textvariable=count_var, width=10, font=("Segoe UI", 10)).pack(anchor="w", pady=(0, 15))

        ttk.Label(frame, text="Filters:", font=("Segoe UI", 11, "bold")).pack(anchor="w")

        filter_frame = ttk.Frame(frame)
        filter_frame.pack(fill=tk.X, pady=10)

        videos_only_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(filter_frame, text="Videos only (no images)", variable=videos_only_var).pack(anchor="w")

        min_likes_var = tk.StringVar(value="0")
        ttk.Label(filter_frame, text="Minimum likes:", font=("Segoe UI", 10)).pack(anchor="w", pady=(5, 0))
        ttk.Entry(filter_frame, textvariable=min_likes_var, width=10).pack(anchor="w", pady=(0, 10))

        def go():
            user = user_entry.get().strip()
            if not user:
                messagebox.showwarning("Warning", "Enter username or URL")
                return
            
            try:
                limit = int(count_var.get())
                min_likes = int(min_likes_var.get())
            except ValueError:
                messagebox.showerror("Error", "Limit and likes must be numbers")
                return
            
            win.destroy()
            threading.Thread(
                target=self.download_user,
                args=(user, limit, videos_only_var.get(), min_likes),
                daemon=True
            ).start()

        ttk.Button(frame, text="📥 Start Batch Download", command=go).pack(pady=20)

    def download_user(self, user, limit, videos_only=False, min_likes=0):
        try:
            if "instagram.com" in user:
                user = user.rstrip("/").split("/")[-1]

            self.log(f"👤 Fetching posts from {user}...")
            profile = instaloader.Profile.from_username(self.loader.context, user)
            posts = [p for p in profile.get_posts()]

            if videos_only:
                posts = [p for p in posts if p.is_video]
            
            if min_likes > 0:
                posts = [p for p in posts if p.likes >= min_likes]
                self.log(f"📊 Filtered to {len(posts)} posts with {min_likes}+ likes")

            if limit > 0:
                posts = posts[:limit]

            for post in posts:
                self.queue.append(f"https://www.instagram.com/p/{post.shortcode}/")

            self.log(f"✓ Added {len(posts)} posts from {user}")
            self.save_queue_backup()
        except Exception as e:
            self.log(f"❌ Error: {e}")

    def browse(self):
        folder = filedialog.askdirectory(title="Select Download Folder")
        if folder:
            self.path_var.set(os.path.abspath(folder))
            self.log(f"📁 Folder: {folder}")

    def show_stats(self):
        win = tk.Toplevel(self.root)
        win.title("Download Statistics")
        win.geometry("450x300")
        win.resizable(False, False)

        frame = ttk.Frame(win, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)

        stats_text = f"""
Session Statistics:
─────────────────────────
Total Downloads: {self.stats.stats['total_downloads']}
Successful: {self.stats.stats['success_count']}
Failed: {self.stats.stats['fail_count']}

Data Downloaded: {self.stats.stats['total_size_mb']:.2f} MB
Avg Speed: {self.stats.stats['avg_speed_mbps']:.2f} Mbps
Total Time: {self.stats.stats['total_time_hours']:.2f} hours

History Entries: {len(self.history.history)}
        """

        ttk.Label(frame, text=stats_text, font=("Courier", 10), justify=tk.LEFT).pack(anchor="w")

        button_frame = ttk.Frame(frame)
        button_frame.pack(fill=tk.X, pady=15)

        def export_data():
            try:
                content = self.history.export()
                with open("download_export.csv", "w") as f:
                    f.write(content)
                messagebox.showinfo("Success", "Exported to download_export.csv")
            except Exception as e:
                messagebox.showerror("Error", str(e))

        def clear_data():
            if messagebox.askyesno("Confirm", "Delete all history?"):
                self.history.clear()
                messagebox.showinfo("Success", "History cleared")

        ttk.Button(button_frame, text="📊 Export CSV", command=export_data).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="🗑️ Clear History", command=clear_data).pack(side=tk.LEFT, padx=5)

    def filter_logs(self):
        win = tk.Toplevel(self.root)
        win.title("Search Logs")
        win.geometry("400x250")
        win.resizable(False, False)

        frame = ttk.Frame(win, padding=15)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="Search for:", font=("Segoe UI", 10)).pack(anchor="w")
        search_var = tk.StringVar()
        search_entry = ttk.Entry(frame, textvariable=search_var, font=("Segoe UI", 10))
        search_entry.pack(fill=tk.X, pady=(0, 10))
        search_entry.focus()

        ttk.Label(frame, text="Results:", font=("Segoe UI", 10)).pack(anchor="w")
        
        results_text = tk.Text(frame, height=10, wrap=tk.WORD)
        results_text.pack(fill=tk.BOTH, expand=True)

        def search():
            query = search_var.get()
            if not query:
                messagebox.showwarning("Warning", "Enter search term")
                return
            
            results_text.config(state="normal")
            results_text.delete("1.0", tk.END)
            
            log_content = self.log_text.get("1.0", tk.END)
            lines = log_content.split("\n")
            matches = [l for l in lines if query.lower() in l.lower()]
            
            results_text.insert(tk.END, "\n".join(matches[-20:]))
            results_text.config(state="disabled")

        ttk.Button(frame, text="🔍 Search", command=search).pack(pady=10)

    def show_notification(self, title, message):
        try:
            import subprocess
            subprocess.Popen([
                "powershell", "-Command",
                f'[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; '
                f'<toast><visual><binding template="ToastText02"><text id="1">{title}</text><text id="2">{message}</text></binding></visual></toast>"@; '
                f'[Windows.UI.Notifications.ToastNotification]::new([xml]$template).Tag = "RDE"; '
                f'[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("RDE").Show([Windows.UI.Notifications.ToastNotification]::new([xml]$template))'
            ], capture_output=True)
        except:
            pass

    def credits(self):
        win = tk.Toplevel(self.root)
        win.title("About RDE")
        win.geometry("600x400")
        win.resizable(False, False)

        frame = ttk.Frame(win, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text=APP_NAME, font=("Segoe UI", 22, "bold")).pack(pady=10)
        ttk.Label(frame, text=f"Version {APP_VERSION}", font=("Segoe UI", 11), foreground="gray").pack()
        ttk.Label(frame, text=f"By {APP_AUTHOR}", font=("Segoe UI", 10)).pack(pady=5)

        ttk.Separator(frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=15)

        features_text = """Note from the dev:
This is a UNSTABLE build of RDE (Codename 'Klondike')
This version has new features and changes that are still being tested.

If you encounter any issues, please report them on our Discord server.
        """
        
        ttk.Label(frame, text=features_text, justify=tk.LEFT, font=("Segoe UI", 9)).pack(anchor="w")

        ttk.Button(
            frame,
            text="🔗 Join Discord",
            command=lambda: webbrowser.open(APP_DISCORD)
        ).pack(pady=15)

if __name__ == "__main__":
    root = tk.Tk()
    app = ReelDownloader(root)
    root.mainloop()
    
    if app.queue:
        app.save_queue_backup()
