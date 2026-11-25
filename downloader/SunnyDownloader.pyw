# -*- coding: utf-8 -*-
"""이 창에서 하는 일: 영상 링크를 목록에 모아두고 편집용 MP4/4K/MP3로 받는 로컬 다운로더(목록·기록 저장)."""
import csv
import datetime
import json
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import tkinter as tk
import urllib.request
import webbrowser
from tkinter import filedialog, messagebox, ttk

import yt_dlp
from yt_dlp.postprocessor.metadataparser import MetadataParserPP

HERE = os.path.dirname(os.path.abspath(__file__))
HOST = socket.gethostname()
SETTINGS = os.path.join(HERE, f"설정_{HOST}.json")  # PC별 설정(드라이브 공유 충돌 방지)
LIST_FILE = os.path.join(HERE, f"목록_{HOST}.json")  # PC별 링크 목록(껐다 켜도 유지)
VERSION = 8  # 사이트의 version.json보다 낮으면 새 버전 공지
UPDATE_URL = "https://sunnyremover.com/downloader/version.json"
APP_FILE = os.path.abspath(__file__)
HISTORY_NAME = "다운로드기록.csv"  # 저장 폴더 안에 쌓이는 출처 기록
FONT = ("맑은 고딕", 10)
OUTTMPL = "%(title).120s.%(ext)s"  # 원래 제목 그대로(같은 이름이 있으면 다시 받지 않음)
TITLE_PREFIX = r"(?i)^\s*(?:video|photo|post|reel|reels|story)\s+by\s+"  # 인스타 등 "Video by 계정" 머리말
URL_RE = re.compile(r"https?://[^\s<>\"']+")

WAIT, RUN, DONE, FAIL = "대기", "받는 중", "완료", "실패"
NO_TITLE = "(제목 확인 중…)"

QUALITIES = {
    "편집용 1080p MP4 (추천)": {
        "format": "bv*[vcodec^=avc1][height<=1080]+ba[ext=m4a]/b[ext=mp4]/bv*[height<=1080]+ba/b",
        "merge_output_format": "mp4",
    },
    "최고화질 원본 MP4 (4K·8K 그대로)": {
        "format": "bv*+ba[ext=m4a]/bv*+ba/b",
        "merge_output_format": "mp4",
    },
    "최고화질 원본 MP4 · 프리미어 호환 변환 (느림)": {
        "format": "bv*+ba/b",
        "merge_output_format": "mkv",
        "postprocessors": [{"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}],
        "postprocessor_args": {"videoconvertor": ["-c:v", "libx264", "-crf", "18", "-preset", "fast",
                                                  "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k"]},
    },
    "최고화질 원본 MKV": {
        "format": "bv*+ba/b",
        "merge_output_format": "mkv",
    },
    "음악만 MP3": {
        "format": "ba/b",
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
        ],
    },
}
QUALITY_ALIASES = {  # 예전 설정 호환
    "최고화질 4K (원본 코덱)": "최고화질 원본 MKV",
    "최고화질 4K MKV (원본 그대로)": "최고화질 원본 MKV",
    "최고화질 4K MP4": "최고화질 원본 MP4 (4K·8K 그대로)",
    "최고화질 4K MP4 · 프리미어 호환 변환 (느림)": "최고화질 원본 MP4 · 프리미어 호환 변환 (느림)",
}
DEFAULT_QUALITY = "편집용 1080p MP4 (추천)"

WORK_ROOTS = [r"K:\SunnyTeam_Work_DesignPC", r"D:\SunnyTeam_Work_Notebook",
              os.path.join(os.path.expanduser("~"), "SunnyTeam_Work_MacMini")]


def default_folder():
    """PC별 표준 작업폴더 아래 _레퍼런스영상, 없으면 사용자 영상 폴더."""
    for root in WORK_ROOTS:
        if os.path.isdir(root):
            return os.path.join(root, "_레퍼런스영상")
    movies = "Movies" if sys.platform == "darwin" else "Videos"
    return os.path.join(os.path.expanduser("~"), movies, "레퍼런스영상")


def extract_urls(text):
    """붙여넣은 글에서 링크만 골라낸다(여러 줄·공백·문장 섞여도 됨)."""
    return [u.rstrip(").,]}>") for u in URL_RE.findall(text or "")]


def friendly_error(exc):
    msg = str(exc)
    low = msg.lower()
    if "drm" in low:
        return "복제 방지(DRM)가 걸린 영상이라 받을 수 없습니다 — 화면 녹화로 대신하세요"
    if "cookie" in low and any(k in low for k in ("decrypt", "could not", "copy", "database", "permission")):
        return ("브라우저 로그인 정보를 읽지 못했습니다 — 그 브라우저 창을 모두 닫고 다시 받거나, "
                "'로그인 필요한 영상'에서 다른 브라우저(파이어폭스 등)를 고르세요")
    if any(k in low for k in ("sign in", "logged-in", "login", "log in", "cookies", "401", "unauthorized")) \
            or ("age" in low and "restrict" in low):
        return ("로그인이 필요한 영상입니다 — 화질 옆 '로그인 필요한 영상'에서 평소 그 사이트에 로그인해 둔 "
                "브라우저를 고른 뒤 다시 받으세요")
    if any(k in low for k in ("private", "unavailable", "unsupported url", "not a valid url",
                              "removed", "does not exist", "unable to download webpage",
                              "http error 404")):
        return "링크를 열 수 없습니다 — 비공개/삭제 영상이거나 주소를 확인하세요"
    if "ffmpeg" in low:
        return "ffmpeg 문제로 합치기에 실패했습니다 — ffmpeg 설치를 확인하세요"
    if "getaddrinfo" in low or "urlopen error" in low or "timed out" in low:
        return "인터넷 연결을 확인하세요"
    return "다운로드 실패 — '엔진 업데이트'를 누른 뒤 다시 시도하세요 (" + msg.strip()[:120] + ")"


def find_ffmpeg():
    """PATH에 없을 때 winget·homebrew 기본 설치 위치까지 찾는다(설치 직후 PATH 미반영 대비)."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    candidates = [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Links", "ffmpeg.exe"),
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
    ]
    return next((c for c in candidates if os.path.isfile(c)), None)


def clean_title(title):
    return re.sub(TITLE_PREFIX, "", title or "").strip()


LOGIN_CHOICES = {"안 씀": None, "크롬": "chrome", "엣지": "edge", "웨일": "whale",
                 "파이어폭스": "firefox", "사파리": "safari"}
LOGIN = {"browser": None}  # 로그인 필요한 영상용: 이 브라우저의 로그인 정보를 빌려 쓴다
VIMEO_RE = re.compile(r"https?://(?:www\.)?vimeo\.com/(?:channels/[^/]+/|groups/[^/]+/videos/|"
                      r"showcase/\d+/video/)?(\d+)(?:/([0-9a-f]{6,}))?")


def normalize_url(url):
    """비메오 일반 주소는 로그인을 요구해서, 로그인 없이 되는 플레이어 주소로 바꾼다."""
    m = VIMEO_RE.match(url)
    if m and not LOGIN["browser"]:
        vid, h = m.group(1), m.group(2)
        return f"https://player.vimeo.com/video/{vid}" + (f"?h={h}" if h else "")
    return url


def base_opts():
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True}
    if LOGIN["browser"]:
        opts["cookiesfrombrowser"] = (LOGIN["browser"],)
    return opts


def fetch_title(url):
    """다운로드 없이 제목만 확인."""
    opts = {**base_opts(), "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(normalize_url(url), download=False, process=False)
    return clean_title((info or {}).get("title"))


def download_one(url, folder, quality, on_progress=None, simulate=False):
    """링크 1개 처리. 성공 시 (파일명, 제목) 반환, 실패 시 예외."""
    opts = dict(QUALITIES[quality])
    title_fix = {"key": "MetadataParser", "when": "pre_process",
                 "actions": [(MetadataParserPP.Actions.REPLACE, "title", TITLE_PREFIX, "")]}
    opts["postprocessors"] = [title_fix] + list(opts.get("postprocessors", []))
    opts.update(base_opts())
    opts.update({
        "outtmpl": os.path.join(folder, OUTTMPL),
        "noprogress": True,
        "windowsfilenames": True,
    })
    url = normalize_url(url)
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        opts["ffmpeg_location"] = ffmpeg
    if simulate:
        opts["simulate"] = True
        opts["skip_download"] = True
    if on_progress:
        opts["progress_hooks"] = [on_progress]
    try:
        return _run_download(opts, url, quality, simulate)
    except yt_dlp.utils.DownloadError as exc:
        if "drm" not in str(exc).lower():
            raise
        # 비메오 등: DASH 화질에만 복제 방지(DRM)가 걸린 경우 → HLS 스트림으로 다시 시도
        h = "[height<=1080]" if quality.startswith("편집용") else ""
        hls = "[protocol^=m3u8]"
        opts["format"] = ("ba" + hls + "/b" + hls) if quality.startswith("음악만") else \
            f"bv*{hls}{h}+ba{hls}/b{hls}{h}"
        return _run_download(opts, url, quality, simulate)


def _run_download(opts, url, quality, simulate):
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=not simulate)
        if info is None:
            raise RuntimeError("no info")
        title = clean_title(info.get("title"))
        if simulate:
            return "(시험) format=" + str(info.get("format_id")), title
        reqs = info.get("requested_downloads") or []
        path = reqs[0].get("filepath") if reqs else None
        if path and quality.startswith("음악만"):
            path = os.path.splitext(path)[0] + ".mp3"
        return (os.path.basename(path) if path else title), title


def append_history(folder, row):
    """저장 폴더의 다운로드기록.csv에 한 줄 추가(엑셀에서 바로 열리게 BOM 포함)."""
    path = os.path.join(folder, HISTORY_NAME)
    is_new = not os.path.exists(path)
    with open(path, "a", encoding="utf-8-sig", newline="") as fp:
        w = csv.writer(fp)
        if is_new:
            w.writerow(["받은 날짜", "제목", "파일명", "화질", "링크"])
        w.writerow(row)


def http_get(url, timeout=8):
    """사이트에서 파일 받기(인증서는 certifi가 있으면 그걸 쓴다)."""
    ctx = None
    try:
        import ssl
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    req = urllib.request.Request(url, headers={"User-Agent": "SunnyDownloader/%d" % VERSION,
                                               "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read()


def check_new_version():
    """새 버전 정보(dict) 또는 None. 인터넷이 안 돼도 조용히 None."""
    try:
        info = json.loads(http_get(UPDATE_URL + "?t=%d" % int(datetime.datetime.now().timestamp())))
        if int(info.get("version", 0)) > VERSION and info.get("pyw"):
            return info
    except Exception:  # noqa: BLE001 - 공지 확인 실패는 사용에 지장 없음
        pass
    return None


def install_new_version(info):
    """새 프로그램 파일을 받아 문법 검사 후 교체(이전 파일은 .bak로 보관)."""
    code = http_get(info["pyw"], timeout=60)
    text = code.decode("utf-8")
    if "class App" not in text:
        raise RuntimeError("받은 파일이 올바르지 않습니다")
    compile(text, APP_FILE, "exec")
    tmp = APP_FILE + ".new"
    with open(tmp, "wb") as fp:
        fp.write(code)
    shutil.copy2(APP_FILE, APP_FILE + ".bak")
    os.replace(tmp, APP_FILE)


def open_path(path):
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


class App:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.busy = False
        self.items = {}  # 목록 행 id -> {url, title, status, file, date}
        cfg = self.load_cfg()
        root.title("Sunny Downloader")
        self.set_icon()
        root.geometry("820x720")
        root.minsize(640, 560)
        root.option_add("*Font", FONT)
        style = ttk.Style()
        style.configure(".", font=FONT)
        style.configure("Treeview", rowheight=26)
        style.configure("Big.TButton", padding=(14, 6))

        self.banner = tk.Frame(root, bg="#ff6a3d")  # 새 버전 공지(평소엔 숨김)
        f = ttk.Frame(root, padding=12)
        f.pack(fill="both", expand=True)
        self.main_frame = f
        self.build_input(f)
        self.build_list(f)
        self.build_options(f, cfg)
        self.build_log(f)

        self.load_list()
        if not find_ffmpeg():
            self.write("[경고] ffmpeg를 찾지 못했습니다. 1080p 영상+음성 합치기에 ffmpeg가 필요합니다.")
        if sys.platform == "darwin" and tk.TkVersion < 8.6:
            self.write("[경고] 맥 기본 파이썬(Tk %s)은 글자 입력이 안 됩니다. 바탕화면 앱으로 실행하거나 "
                       "처음설치_맥.command를 다시 실행하세요." % tk.TkVersion)
        self.write("링크 칸에 붙여넣고(Ctrl+V) 엔터를 누르면 목록에 쌓입니다. "
                   "다 모았으면 '목록 다운로드'를 누르세요.")
        self.root.after(100, self.pump)
        self.root.after(300, self.bring_to_front)
        if not os.environ.get("SUNNY_NO_UPDATE"):
            threading.Thread(target=lambda: self.q.put(("newver", check_new_version())), daemon=True).start()

    # ---- 새 버전 공지·업데이트 ----
    def show_new_version(self, info):
        if not info:
            return
        self.new_info = info
        for w in self.banner.winfo_children():
            w.destroy()
        msg = f"새 버전 v{info['version']}이 나왔습니다.  {info.get('notes', '')}".strip()
        tk.Label(self.banner, text=msg, bg="#ff6a3d", fg="white", anchor="w",
                 font=(FONT[0], 11, "bold"), wraplength=560, justify="left").pack(side="left", padx=12, pady=8)
        self.btn_newver = ttk.Button(self.banner, text="지금 업데이트", command=self.apply_new_version)
        self.btn_newver.pack(side="right", padx=12, pady=6)
        self.banner.pack(fill="x", before=self.main_frame)
        self.write(f"[공지] {msg}")

    def apply_new_version(self):
        if self.busy:
            messagebox.showinfo("안내", "받는 중에는 업데이트할 수 없습니다. 다운로드가 끝난 뒤 눌러 주세요.")
            return
        self.set_busy(True)
        self.btn_newver.configure(state="disabled", text="업데이트 중...")
        threading.Thread(target=self.new_version_worker, daemon=True).start()

    def new_version_worker(self):
        try:
            install_new_version(self.new_info)
            self.q.put(("restart", None))
        except Exception as exc:  # noqa: BLE001
            self.q.put(("log", f"업데이트 실패: {exc} — 사이트에서 새 압축을 받아 주세요."))
            self.q.put(("done", None))

    def restart(self):
        self.save_list()
        messagebox.showinfo("업데이트 완료", "새 버전으로 바꿨습니다. 프로그램을 다시 켭니다.")
        subprocess.Popen([sys.executable, APP_FILE], cwd=HERE)
        self.root.destroy()

    # ---- 화면 만들기 ----
    def set_icon(self):
        try:
            if sys.platform == "win32":
                self.root.iconbitmap(os.path.join(HERE, "아이콘.ico"))
            else:
                self._icon = tk.PhotoImage(file=os.path.join(HERE, "아이콘.png")).subsample(8)
                self.root.iconphoto(True, self._icon)
        except tk.TclError:
            pass

    def build_input(self, f):
        row = ttk.Frame(f)
        row.pack(fill="x")
        ttk.Label(row, text="링크").pack(side="left")
        self.entry = ttk.Entry(row, font=(FONT[0], 11))
        self.entry.pack(side="left", fill="x", expand=True, padx=(6, 6), ipady=4)
        self.entry.bind("<Return>", lambda e: self.add_from_entry())
        ttk.Button(row, text="추가", style="Big.TButton", command=self.add_from_entry).pack(side="left")
        ttk.Button(row, text="복사한 링크 바로 추가",
                   command=self.paste_clipboard).pack(side="left", padx=(6, 0))
        self.bind_paste_keys(self.entry, into_entry=True)

    def build_list(self, f):
        box = ttk.Frame(f)
        box.pack(fill="both", expand=True, pady=(8, 2))
        cols = (("status", "상태", 90), ("title", "제목", 330), ("date", "받은 날", 90), ("url", "링크", 230))
        self.tree = ttk.Treeview(box, columns=[c[0] for c in cols], show="headings",
                                 height=10, selectmode="extended")
        for key, text, width in cols:
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor="w", stretch=key in ("title", "url"))
        sb = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.tag_configure(DONE, foreground="#1a7f37")
        self.tree.tag_configure(FAIL, foreground="#c0392b")
        self.tree.tag_configure(RUN, foreground="#1f5fbf")
        self.tree.bind("<Double-1>", lambda e: self.open_selected_link())
        self.tree.bind("<Delete>", lambda e: self.remove_selected())
        self.tree.bind("<BackSpace>", lambda e: self.remove_selected())
        self.bind_paste_keys(self.tree)

        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="링크 열어보기", command=self.open_selected_link)
        menu.add_command(label="다시 받기(대기로)", command=self.retry_selected)
        menu.add_command(label="삭제", command=self.remove_selected)

        def popup(event):
            iid = self.tree.identify_row(event.y)
            if iid and iid not in self.tree.selection():
                self.tree.selection_set(iid)
            menu.tk_popup(event.x_root, event.y_root)
            return "break"

        for seq in ("<Button-2>", "<Button-3>", "<Control-Button-1>"):
            self.tree.bind(seq, popup)

        row = ttk.Frame(f)
        row.pack(fill="x", pady=(2, 6))
        self.count = ttk.Label(row, text="")
        self.count.pack(side="left")
        ttk.Button(row, text="모두 지우기", command=self.clear_all).pack(side="right")
        ttk.Button(row, text="완료 항목 정리", command=self.clear_done).pack(side="right", padx=4)
        ttk.Button(row, text="선택 삭제", command=self.remove_selected).pack(side="right")

    def build_options(self, f, cfg):
        row = ttk.Frame(f)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="화질").pack(side="left")
        saved = cfg.get("quality", DEFAULT_QUALITY)
        self.quality = tk.StringVar(value=QUALITY_ALIASES.get(saved, saved))
        if self.quality.get() not in QUALITIES:
            self.quality.set(DEFAULT_QUALITY)
        ttk.Combobox(row, textvariable=self.quality, values=list(QUALITIES), state="readonly",
                     width=40).pack(side="left", padx=8)
        self.login = tk.StringVar(value=cfg.get("login", "안 씀"))
        if self.login.get() not in LOGIN_CHOICES:
            self.login.set("안 씀")
        LOGIN["browser"] = LOGIN_CHOICES[self.login.get()]
        box = ttk.Combobox(row, textvariable=self.login, values=list(LOGIN_CHOICES), state="readonly", width=9)
        box.pack(side="right")
        box.bind("<<ComboboxSelected>>", lambda e: self.on_login_change())
        ttk.Label(row, text="로그인 필요한 영상").pack(side="right", padx=(0, 6))

        row = ttk.Frame(f)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text="저장 폴더").pack(side="left")
        self.folder = tk.StringVar(value=cfg.get("folder") or default_folder())
        ttk.Entry(row, textvariable=self.folder).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(row, text="폴더 선택", command=self.pick_folder).pack(side="left", padx=2)
        ttk.Button(row, text="폴더 열기", command=self.open_folder).pack(side="left", padx=2)
        ttk.Button(row, text="다운로드 기록", command=self.open_history).pack(side="left", padx=2)

        row = ttk.Frame(f)
        row.pack(fill="x", pady=6)
        self.btn_dl = ttk.Button(row, text="목록 다운로드", style="Big.TButton", command=self.start)
        self.btn_dl.pack(side="left")
        ttk.Label(row, text="대기·실패 항목만 차례로 받습니다").pack(side="left", padx=8)
        self.btn_up = ttk.Button(row, text="안 받아질 때: 엔진 업데이트", command=self.update)
        self.btn_up.pack(side="right")

    def build_log(self, f):
        self.bar = ttk.Progressbar(f, maximum=100)
        self.bar.pack(fill="x", pady=4)
        self.status = ttk.Label(f, text="대기 중")
        self.status.pack(anchor="w")
        self.log = tk.Text(f, height=6, font=FONT, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=False, pady=(6, 0))

    def bring_to_front(self):
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(200, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()
        self.entry.focus_set()

    # ---- 붙여넣기 ----
    def bind_paste_keys(self, widget, into_entry=False):
        """입력칸: Ctrl/Cmd+V = 칸에 글자를 붙여넣기(보통 프로그램과 같게). 목록: 바로 목록에 추가."""
        action = self.paste_into_entry if into_entry else self.paste_clipboard
        widget.bind("<<Paste>>", lambda e: action() or "break")

        def handler(event):
            return self.on_paste_key(event, action)

        widget.bind("<Control-Key>", handler)  # 한글 입력 상태(ㅍ)에서도 붙여넣기
        if sys.platform == "darwin":
            widget.bind("<Command-Key>", handler)

    def on_paste_key(self, event, action):
        keysym = (event.keysym or "").lower()
        if keysym == "v" or event.char in ("v", "V", "ㅍ", "\x16") or keysym.startswith("hangul_p"):
            action()
            return "break"
        return None

    def paste_into_entry(self):
        """입력칸에 그대로 붙여넣기. 링크가 여러 개 섞인 글이면 바로 목록에 넣는다."""
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            return
        urls = extract_urls(text)
        if len(urls) > 1:
            self.add_urls(urls)
            return
        try:
            self.entry.delete("sel.first", "sel.last")
        except tk.TclError:
            pass
        self.entry.insert("insert", text.strip())
        if urls:
            self.write("[알림] 링크를 넣었습니다. 엔터나 '추가'를 누르면 목록에 들어갑니다.")

    def paste_clipboard(self):
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            text = ""
        urls = extract_urls(text)
        if not urls:
            self.write("[알림] 복사된 링크가 없습니다. 브라우저 주소창에서 링크를 복사한 뒤 다시 누르세요.")
            return
        self.add_urls(urls)

    def add_from_entry(self):
        urls = extract_urls(self.entry.get())
        if not urls:
            self.write("[알림] http로 시작하는 링크를 넣어 주세요.")
            return
        self.entry.delete(0, "end")
        self.add_urls(urls)

    # ---- 목록 ----
    def add_urls(self, urls):
        known = {it["url"] for it in self.items.values()}
        added = []
        for url in urls:
            if url in known:
                continue
            known.add(url)
            added.append((self.insert_item({"url": url, "title": "", "status": WAIT}), url))
        skipped = len(urls) - len(added)
        msg = f"목록에 {len(added)}개 추가"
        if skipped:
            msg += f" (이미 목록에 있는 {skipped}개 제외)"
        self.write(msg)
        if added:
            self.after_list_change()
            threading.Thread(target=self.title_worker, args=(added,), daemon=True).start()

    def insert_item(self, item):
        iid = self.tree.insert("", "end")
        self.items[iid] = {"url": item["url"], "title": item.get("title", ""),
                           "status": item.get("status", WAIT), "file": item.get("file", ""),
                           "date": item.get("date", "")}
        self.render(iid)
        return iid

    def render(self, iid):
        it = self.items[iid]
        tag = it["status"].split()[0] if it["status"] else WAIT
        tag = RUN if tag == "받는" else tag
        self.tree.item(iid, values=(it["status"], it["title"] or NO_TITLE, it["date"], it["url"]),
                       tags=(tag,))

    def set_item(self, iid, changes):
        if iid not in self.items:  # 받는 도중 목록에서 지운 경우
            return
        self.items[iid] = {**self.items[iid], **changes}
        self.render(iid)

    def selected(self):
        return [i for i in self.tree.selection() if i in self.items]

    def remove_selected(self):
        for iid in self.selected():
            if self.busy and self.items[iid]["status"].startswith(RUN):
                continue  # 지금 받는 항목은 남겨둔다
            self.tree.delete(iid)
            del self.items[iid]
        self.after_list_change()

    def retry_selected(self):
        for iid in self.selected():
            if not self.items[iid]["status"].startswith(RUN):
                self.set_item(iid, {"status": WAIT})
        self.after_list_change()

    def clear_done(self):
        for iid in [i for i, it in self.items.items() if it["status"] == DONE]:
            self.tree.delete(iid)
            del self.items[iid]
        self.after_list_change()

    def clear_all(self):
        if self.busy:
            messagebox.showinfo("안내", "받는 중에는 모두 지울 수 없습니다.")
            return
        if self.items and messagebox.askyesno("확인", "목록을 모두 지울까요?\n(받은 파일과 다운로드 기록은 그대로 남습니다)"):
            self.tree.delete(*self.tree.get_children())
            self.items.clear()
            self.after_list_change()

    def open_selected_link(self):
        for iid in self.selected()[:3]:
            webbrowser.open(self.items[iid]["url"])

    def after_list_change(self):
        self.save_list()
        counts = {}
        for it in self.items.values():
            key = RUN if it["status"].startswith(RUN) else it["status"]
            counts[key] = counts.get(key, 0) + 1
        self.count.configure(text=f"전체 {len(self.items)}개 · 대기 {counts.get(WAIT, 0)} · "
                                  f"완료 {counts.get(DONE, 0)} · 실패 {counts.get(FAIL, 0)}")

    def load_list(self):
        try:
            with open(LIST_FILE, encoding="utf-8") as fp:
                saved = json.load(fp)
        except (OSError, ValueError):
            saved = []
        missing = []
        for item in saved if isinstance(saved, list) else []:
            if not isinstance(item, dict) or not item.get("url"):
                continue
            if item.get("status", "").startswith(RUN):
                item = {**item, "status": WAIT}  # 지난번에 받다 끊긴 항목
            iid = self.insert_item(item)
            if not item.get("title"):
                missing.append((iid, item["url"]))
        self.after_list_change()
        if missing:
            threading.Thread(target=self.title_worker, args=(missing,), daemon=True).start()

    def save_list(self):
        data = [self.items[i] for i in self.tree.get_children() if i in self.items]
        try:
            with open(LIST_FILE, "w", encoding="utf-8") as fp:
                json.dump(data, fp, ensure_ascii=False, indent=1)
        except OSError:
            pass

    def title_worker(self, pairs):
        for iid, url in pairs:
            try:
                title = fetch_title(url)
            except Exception:  # noqa: BLE001 - 제목은 못 찾아도 다운로드는 시도
                title = ""
            self.q.put(("item", (iid, {"title": title or "(제목 확인 실패 — 링크를 확인하세요)"}), True))

    # ---- 설정 ----
    def load_cfg(self):
        try:
            with open(SETTINGS, encoding="utf-8") as fp:
                return json.load(fp)
        except (OSError, ValueError):
            return {}

    def on_login_change(self):
        LOGIN["browser"] = LOGIN_CHOICES[self.login.get()]
        self.save_cfg()
        if LOGIN["browser"]:
            self.write(f"[알림] 로그인 필요한 영상은 {self.login.get()}의 로그인 정보로 받습니다. "
                       "그 브라우저에서 해당 사이트(인스타·링크드인 등)에 로그인돼 있어야 합니다.")

    def save_cfg(self):
        try:
            with open(SETTINGS, "w", encoding="utf-8") as fp:
                json.dump({"folder": self.folder.get(), "quality": self.quality.get(), "login": self.login.get()},
                          fp, ensure_ascii=False, indent=2)
        except OSError:
            pass

    # ---- UI 도우미 ----
    def write(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def pick_folder(self):
        d = filedialog.askdirectory(initialdir=self.folder.get() or None)
        if d:
            self.folder.set(os.path.normpath(d))
            self.save_cfg()

    def open_folder(self):
        d = self.folder.get()
        try:
            os.makedirs(d, exist_ok=True)
            open_path(d)
        except OSError:
            messagebox.showerror("오류", "폴더를 열 수 없습니다. 폴더를 다시 선택하세요.")

    def open_history(self):
        path = os.path.join(self.folder.get(), HISTORY_NAME)
        if not os.path.exists(path):
            messagebox.showinfo("안내", "아직 기록이 없습니다. 받은 영상이 생기면 저장 폴더에\n"
                                      f"'{HISTORY_NAME}'로 날짜·제목·링크가 쌓입니다.")
            return
        open_path(path)

    def set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.btn_dl.configure(state=state)
        self.btn_up.configure(state=state)

    def pump(self):
        """작업 스레드가 보낸 메시지를 GUI 스레드에서 처리."""
        try:
            while True:
                kind, val, *rest = self.q.get_nowait()
                if kind == "log":
                    self.write(val)
                elif kind == "bar":
                    self.bar["value"] = val[0]
                    self.status.configure(text=val[1])
                elif kind == "item":
                    self.set_item(*val)
                    if rest and rest[0]:
                        self.after_list_change()
                elif kind == "newver":
                    self.show_new_version(val)
                elif kind == "restart":
                    self.restart()
                    return
                elif kind == "done":
                    self.set_busy(False)
                    self.status.configure(text="대기 중")
                    self.after_list_change()
        except queue.Empty:
            pass
        self.root.after(100, self.pump)

    # ---- 다운로드 ----
    def start(self):
        targets = [(i, self.items[i]["url"]) for i in self.tree.get_children()
                   if i in self.items and self.items[i]["status"] in (WAIT, FAIL)]
        if not targets:
            messagebox.showinfo("안내", "받을 항목이 없습니다.\n링크를 목록에 추가하세요. (대기·실패 항목만 받습니다)")
            return
        folder = self.folder.get().strip()
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError:
            messagebox.showerror("오류", "저장 폴더를 만들 수 없습니다. 폴더를 다시 선택하세요.")
            return
        self.save_cfg()
        self.set_busy(True)
        threading.Thread(target=self.worker, args=(targets, folder, self.quality.get()),
                         daemon=True).start()

    def worker(self, targets, folder, quality, simulate=False):
        ok = 0
        for n, (iid, url) in enumerate(targets, 1):
            last = {"pct": -1}

            def hook(d, iid=iid, last=last):
                if d.get("status") == "downloading":
                    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                    pct = int(d.get("downloaded_bytes", 0) * 100 / total) if total else 0
                    if pct == last["pct"]:
                        return
                    last["pct"] = pct
                    speed = d.get("speed")
                    sp = f"{speed / 1048576:.1f} MB/s" if speed else "-"
                    self.q.put(("bar", (pct, f"[{n}/{len(targets)}] 받는 중 {pct}%  ({sp})")))
                    self.q.put(("item", (iid, {"status": f"{RUN} {pct}%"})))
                elif d.get("status") == "finished":
                    self.q.put(("bar", (100, f"[{n}/{len(targets)}] 합치는 중...")))

            self.q.put(("item", (iid, {"status": RUN}), True))
            self.q.put(("bar", (0, f"[{n}/{len(targets)}] 준비 중...")))
            try:
                name, title = download_one(url, folder, quality, hook, simulate=simulate)
                today = datetime.date.today().isoformat()
                changes = {"status": DONE, "file": name, "date": today}
                if title:
                    changes["title"] = title
                self.q.put(("item", (iid, changes), True))
                if not simulate:
                    append_history(folder, [today, title, name, quality, url])
                self.q.put(("log", f"완료: {name}"))
                ok += 1
            except Exception as exc:  # noqa: BLE001 - 링크별로 사용자에게 보여주고 계속
                self.q.put(("item", (iid, {"status": FAIL}), True))
                self.q.put(("log", f"실패: {url}\n   → {friendly_error(exc)}"))
        self.q.put(("log", f"== 끝: {ok}/{len(targets)}개 성공 =="))
        self.q.put(("done", None))

    # ---- 엔진 업데이트 ----
    def update(self):
        self.set_busy(True)
        self.write("다운로드 엔진(yt-dlp) 업데이트 중... 잠시만 기다려 주세요.")
        threading.Thread(target=self.update_worker, daemon=True).start()

    def update_worker(self):
        py = sys.executable
        if py.lower().endswith("pythonw.exe"):
            py = py[:-len("pythonw.exe")] + "python.exe"
        flags = 0x08000000 if sys.platform == "win32" else 0  # 윈도우: 검은 창 숨김
        try:
            r = subprocess.run([py, "-m", "pip", "install", "-U", "yt-dlp"], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=300,
                               creationflags=flags)
            tail = (r.stdout or r.stderr).strip().splitlines()[-3:]
            self.q.put(("log", "\n".join(tail)))
            self.q.put(("log", "업데이트 완료. 프로그램을 껐다 켜면 적용됩니다." if r.returncode == 0
                        else "업데이트 실패 — 인터넷 연결을 확인하세요."))
        except Exception as exc:  # noqa: BLE001
            self.q.put(("log", f"업데이트 실패: {exc}"))
        self.q.put(("done", None))


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
