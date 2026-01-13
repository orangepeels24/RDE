import os
import re
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime
import concurrent.futures
import instaloader
import requests
import webbrowser

APP_NAME = "RDE"
APP_AUTHOR = "Orangepeels24"
APP_DISCORD = "https://discord.gg/RnY4Xk29Nq"
APP_VERSION = "0.9.26 [beta] [stable]"

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

class ReelDownloader:
    def __init__(self, root):
        self.root = root
        self.root.title(f"{APP_NAME} v{APP_VERSION}")
        self.root.geometry("960x700")

        self.set_favicon()

        self.loader = create_loader()
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=3)

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

        self.build_ui()

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

    def build_ui(self):
        style = ttk.Style()
        style.theme_use("clam")

        root_frame = ttk.Frame(self.root, padding=10)
        root_frame.pack(fill=tk.BOTH, expand=True)

        url_box = ttk.LabelFrame(root_frame, text="Instagram Reel URLs")
        url_box.pack(fill=tk.X, pady=5)

        self.url_text = tk.Text(url_box, height=6)
        self.url_text.pack(fill=tk.X, padx=6, pady=6)

        url_btns = ttk.Frame(url_box)
        url_btns.pack(fill=tk.X, padx=6, pady=(0, 6))

        ttk.Button(url_btns, text="Add to Queue", command=self.add_urls).pack(side=tk.LEFT)
        ttk.Button(
            url_btns,
            text="Clear",
            command=lambda: self.url_text.delete("1.0", tk.END)
        ).pack(side=tk.LEFT, padx=5)
        ttk.Button(
            url_btns,
            text="Download User Reels",
            command=self.user_dialog
        ).pack(side=tk.LEFT, padx=5)

        path_box = ttk.LabelFrame(root_frame, text="Download Folder")
        path_box.pack(fill=tk.X, pady=5)

        self.path_var = tk.StringVar(value=self.download_path)
        ttk.Entry(path_box, textvariable=self.path_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=6
        )
        ttk.Button(path_box, text="Browse", command=self.browse).pack(
            side=tk.RIGHT, padx=6
        )

        ctrl_frame = ttk.Frame(root_frame)
        ctrl_frame.pack(fill=tk.X, pady=5)

        self.start_btn = ttk.Button(ctrl_frame, text="Start Download", command=self.start)
        self.start_btn.pack(side=tk.LEFT)

        self.stop_btn = ttk.Button(
            ctrl_frame,
            text="Stop",
            command=self.stop,
            state=tk.DISABLED
        )
        self.stop_btn.pack(side=tk.LEFT, padx=5)

        ttk.Button(ctrl_frame, text="Retry Failed", command=self.retry_failed).pack(
            side=tk.LEFT
        )
        ttk.Button(ctrl_frame, text="Credits", command=self.credits).pack(
            side=tk.RIGHT
        )

        self.progress = ttk.Progressbar(root_frame, mode="determinate")
        self.progress.pack(fill=tk.X, pady=5)

        log_box = ttk.LabelFrame(root_frame, text="Log")
        log_box.pack(fill=tk.BOTH, expand=True, pady=5)

        self.log_text = tk.Text(log_box, state="disabled")
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scroll = ttk.Scrollbar(log_box, command=self.log_text.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.log_text.config(yscrollcommand=scroll.set)

    def log(self, msg):
        self.log_text.config(state="normal")
        self.log_text.insert(
            tk.END,
            f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n"
        )
        self.log_text.see(tk.END)
        self.log_text.config(state="disabled")

    def add_urls(self):
        urls = [
            line.strip()
            for line in self.url_text.get("1.0", tk.END).splitlines()
            if "instagram.com" in line
        ]
        self.queue.extend(urls)
        self.log(f"Added {len(urls)} URL(s) to queue. Queue size: {len(self.queue)}")
        self.url_text.delete("1.0", tk.END)

    def start(self):
        if not self.queue:
            messagebox.showinfo("Info", "Queue is empty")
            return

        self.download_path = os.path.abspath(self.path_var.get())
        os.makedirs(self.download_path, exist_ok=True)

        self.completed = 0
        self.progress["maximum"] = len(self.queue)
        self.progress["value"] = 0
        self.stop_flag = False

        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)

        for _ in range(min(3, len(self.queue))):
            self.next_download()

    def stop(self):
        self.stop_flag = True
        self.log("Stopping downloads...")

    def retry_failed(self):
        if not self.failed:
            self.log("No failed downloads to retry")
            return
        self.queue.extend(self.failed)
        self.failed.clear()
        self.start()

    def next_download(self):
        if self.stop_flag or not self.queue:
            self.finish()
            return
        url = self.queue.pop(0)
        future = self.executor.submit(self.download_reel_manual, url)
        future.add_done_callback(self.download_done)

    def download_done(self, future):
        try:
            ok, url = future.result()
            if not ok:
                self.failed.append(url)
        except Exception as e:
            self.log(f"Download thread error: {e}")

        self.completed += 1
        self.progress["value"] = self.completed

        if self.queue:
            self.next_download()
        else:
            self.finish()

    def finish(self):
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.log("All downloads finished")

    def download_reel_manual(self, url):
        try:
            m = re.search(r"/(reel|p)/([^/]+)", url)
            if not m:
                self.log(f"Invalid URL: {url}")
                return False, url

            shortcode = m.group(2)
            post = instaloader.Post.from_shortcode(self.loader.context, shortcode)

            filename = os.path.join(self.download_path, f"{shortcode}.mp4")

            if os.path.exists(filename):
                os.remove(filename)

            self.log(f"Downloading {shortcode}...")
            r = requests.get(post.video_url, stream=True)
            r.raise_for_status()

            with open(filename, "wb") as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)

            self.log(f"Downloaded {shortcode}.mp4")
            return True, url

        except Exception as e:
            self.log(f"Error downloading {url}: {e}")
            return False, url

    def user_dialog(self):
        win = tk.Toplevel(self.root)
        win.title("Download User Reels")
        win.resizable(False, False)

        frame = ttk.Frame(win, padding=10)
        frame.pack(fill=tk.BOTH)

        ttk.Label(frame, text="Username or Profile URL").pack(anchor="w")
        user_entry = ttk.Entry(frame, width=36)
        user_entry.pack(pady=4)

        ttk.Label(frame, text="Max reels (0 = all)").pack(anchor="w")
        count_entry = ttk.Entry(frame, width=10)
        count_entry.insert(0, "10")
        count_entry.pack(pady=4)

        def go():
            user = user_entry.get().strip()
            limit = int(count_entry.get())
            win.destroy()
            threading.Thread(
                target=self.download_user,
                args=(user, limit),
                daemon=True
            ).start()

        ttk.Button(frame, text="Start", command=go).pack(pady=10)

    def download_user(self, user, limit):
        if "instagram.com" in user:
            user = user.rstrip("/").split("/")[-1]

        self.log(f"Fetching reels from {user}...")
        profile = instaloader.Profile.from_username(self.loader.context, user)
        posts = [p for p in profile.get_posts() if p.is_video]

        if limit > 0:
            posts = posts[:limit]

        for post in posts:
            self.queue.append(f"https://www.instagram.com/p/{post.shortcode}/")

        self.log(f"Added {len(posts)} reels from {user} to queue")

    def browse(self):
        folder = filedialog.askdirectory()
        if folder:
            self.path_var.set(os.path.abspath(folder))

    def credits(self):
        win = tk.Toplevel(self.root)
        win.title("Credits")
        win.geometry("500x180")
        win.resizable(False, False)

        ttk.Label(win, text=APP_NAME, font=("Segoe UI", 14, "bold")).pack(pady=8)
        ttk.Label(win, text=f"Version {APP_VERSION}").pack()
        ttk.Label(win, text=f"By {APP_AUTHOR}").pack()

        ttk.Button(
            win,
            text="Discord",
            command=lambda: webbrowser.open(APP_DISCORD)
        ).pack(pady=12)

if __name__ == "__main__":
    root = tk.Tk()
    ReelDownloader(root)
    root.mainloop()
