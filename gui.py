#!/usr/bin/env python3
"""
動画内容解析のデスクトップGUI。
フレームごとに個別解析し、横スクロールで画像と解析結果を並べて表示する。

UI の配色・余白は Google Labs stitch-skills の例 DESIGN.md
（docs/google-labs-stitch-skills-DESIGN.md）を参照して調整している。
"""
import os
import sys
import threading
import base64
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from dotenv import load_dotenv
import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.video_analyzer import (
    analyze_video,
    extract_frames_every_n_seconds,
    get_summary_from_frame_results,
)
from app.logger import log_event

load_dotenv()
log_event("gui_start", {"pid": os.getpid()})

# 解析は成功したがモデルが空文字のみ返したときにテキスト欄へ表示する案内
# 総評の人格（キー → プルダウン表示名）。先頭が既定。
SUMMARY_PERSONA_CHOICES: list[tuple[str, str]] = [
    ("neutral", "標準（淡々・分析的）"),
    ("calm_reviewer", "冷静なレビュアー"),
    ("concise", "極簡潔・編集者"),
    ("narrative", "ナレーション風"),
    ("otaku_girl_lewd", "興味津々オタク女子"),
]
_SUMMARY_LABEL_TO_KEY = {label: key for key, label in SUMMARY_PERSONA_CHOICES}

EMPTY_FRAME_RESULT_PLACEHOLDER = (
    "（モデルから本文が返りませんでした。ラベルは「完了（本文なし）」と表示されます。）\n\n"
    "想定されること:\n"
    "・モデルが空の content のみを返した\n"
    "・コンテンツやモデル設定により本文が省略・抑制された\n"
    "・一時的な Ollama / API の不整合\n\n"
    "試せること: .env の OLLAMA_MODEL 変更、Ollama の更新、同じ動画で再実行。"
)

INTERMEDIATE_EMPTY_PLACEHOLDER = (
    "（ビジョン段階の本文が空です。翻訳後に文字が入る場合があります。）"
)


def _format_frame_result_for_ui(result: str) -> tuple[str, str]:
    """
    (ラベルに付ける状態文, テキスト欄に表示する文字列)。
    エラー行はそのまま。本文が空でエラーでない場合はプレースホルダーを表示する。
    """
    raw = (result or "").strip()
    if raw.startswith("エラー:"):
        return "エラー", raw
    if not raw:
        return "完了（本文なし）", EMPTY_FRAME_RESULT_PLACEHOLDER
    return "完了", raw


def _make_text_readonly(t: tk.Text) -> None:
    """Text を見た目そのままに編集不可にする（DISABLED を使わない）。"""
    def _break(_e):
        return "break"
    for seq in ("<Key>", "<<Paste>>", "<<Cut>>", "<<Clear>>"):
        t.bind(seq, _break)
    t.configure(takefocus=0)


def _truncate_for_log(text: str, limit: int = 2000) -> str:
    """ログ肥大化防止: 先頭 limit 文字だけ残す。"""
    s = text or ""
    if len(s) <= limit:
        return s
    return s[:limit] + f"\n... (truncated, total_chars={len(s)})"


def _set_text_height_to_content(t: tk.Text, content: str, *, min_lines: int = 4, max_lines: int = 24) -> None:
    """内容の行数に合わせて Text の高さ（行数）を調整する。"""
    s = (content or "").strip("\n")
    lines = (s.count("\n") + 1) if s else 1
    height = max(min_lines, min(max_lines, lines))
    try:
        t.configure(height=height)
    except Exception:
        pass


# Google Labs「design-md」例のカラートークン（docs/google-labs-stitch-skills-DESIGN.md）
_DESIGN = {
    "bg": "#FCFAFA",
    "surface": "#F5F5F5",
    "accent": "#294056",
    "accent_hover": "#1f313f",
    "text_primary": "#2C2C2C",
    "text_secondary": "#6B6B6B",
    "border": "#E0E0E0",
    "white": "#FFFFFF",
}


def get_video_info(video_path: str | Path) -> dict | None:
    """動画の長さ・解像度などを返す。取得失敗時は None。"""
    path = Path(video_path)
    if not path.exists() or not path.is_file():
        return None
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return None
    try:
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        duration_sec = (frame_count / fps) if fps > 0 else 0
        return {
            "path": path,
            "name": path.name,
            "duration_sec": duration_sec,
            "width": w,
            "height": h,
            "frame_count": frame_count,
            "fps": fps,
        }
    finally:
        cap.release()


def run_analysis_per_frame(
    video_path: Path,
    config: dict,
    interval_seconds: float,
    on_progress: Callable[[str], None],
    on_frames_ready: Callable[[list[bytes]], None],
    on_frame_start_index: Callable[[int, int], None],
    on_frame_intermediate: Callable[[int, str], None] | None,
    on_frame_result: Callable[[int, str], None],
    on_complete: Callable[[int, int, bool], None],
    on_error: Callable[[str], None],
    should_cancel: Callable[[], bool] | None = None,
) -> None:
    """
    フレームを抽出し、on_frames_ready で渡したあと、
    画像1枚ごとに Ollama で解析する。前の1枚の解析が完了するまで次の画像の解析は開始しない（厳密に順次実行）。
    on_frame_start_index(current_1based, total) で現在どのフレームを解析中か通知する。
    interval_seconds: 画像抽出間隔（秒）。例: 2, 5, 10, 30。
    """
    try:
        on_progress("フレームを抽出しています…")
        frames = extract_frames_every_n_seconds(video_path, interval_seconds=interval_seconds)
        on_frames_ready(frames)
        base_url = config.get("ollama_base_url", "http://localhost:11434")
        model = config.get("ollama_model", "llava")
        japanese_model = config.get("ollama_japanese_model")
        n = len(frames)
        error_count = 0
        cancelled = False
        for i, frame_jpeg in enumerate(frames):
            if should_cancel is not None and should_cancel():
                cancelled = True
                break
            current = i + 1
            on_frame_start_index(current, n)
            on_progress(f"フレーム {current}/{n} を解析中…")
            try:
                # 第1段階: 英語要約（翻訳前）を取得して一旦表示
                from app.video_analyzer import _analyze_video_english_only, _translate_report_to_japanese

                english = _analyze_video_english_only(
                    [frame_jpeg],
                    base_url=base_url,
                    model=model,
                )
                if should_cancel is not None and should_cancel():
                    cancelled = True
                    break
                if on_frame_intermediate is not None and japanese_model:
                    on_frame_intermediate(i, english or "")

                # 第2段階: 日本語翻訳（指定があれば）。結果は最終版として表示。
                if japanese_model:
                    on_progress(f"フレーム {current}/{n} を翻訳中…")
                    result = _translate_report_to_japanese(
                        english or "",
                        base_url=base_url,
                        japanese_model=japanese_model,
                    )
                else:
                    result = english
                on_frame_result(i, result or "")
            except Exception as e:
                error_count += 1
                on_frame_result(i, f"エラー: {e}")
        on_complete(n, error_count, cancelled)
    except Exception as e:
        on_error(str(e))


class VideoAnalyzerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("動画内容解析")
        self.minsize(640, 560)
        self.geometry("900x720")

        self.video_path_var = tk.StringVar()
        self.status_var = tk.StringVar()
        self.analyze_btn: ttk.Button | None = None
        self._card_images: list[tk.PhotoImage] = []
        self._frame_cards: list[dict] = []
        self._scroll_inner: ttk.Frame | None = None
        self._scroll_canvas: tk.Canvas | None = None
        # 旧UIでは画像/結果を別エリアに分割していたが、現行は同一エリアに統合する
        self._images_inner: ttk.Frame | None = None
        self._images_canvas: tk.Canvas | None = None
        self._results_inner: ttk.Frame | None = None
        self._results_canvas: tk.Canvas | None = None
        self._results_hscroll: ttk.Scrollbar | None = None
        self._info_var = tk.StringVar(value="動画を選択してください")
        self._progress_status_var = tk.StringVar(value="")
        self._progress_bar: ttk.Progressbar | None = None
        self._progress_frame: ttk.Frame | None = None
        self._cancel_btn: ttk.Button | None = None
        self._cancel_requested = False
        self._interval_var = tk.StringVar(value="10")
        self._summary_window: tk.Toplevel | None = None
        self._summary_window_btn: ttk.Button | None = None
        self._summary_btn: ttk.Button | None = None
        self._summary_text: tk.Text | None = None
        self._summary_placeholder_var = tk.StringVar(value="総評ウィンドウで生成結果を表示します。")
        self._summary_persona_var = tk.StringVar(value=SUMMARY_PERSONA_CHOICES[0][1])
        # カード作成前に届いた解析結果を保持（レース対策）
        self._pending_frame_results: dict[int, str] = {}
        self._design = _DESIGN

        self._apply_design_system()
        self._build_ui()

    def _apply_design_system(self) -> None:
        """docs/google-labs-stitch-skills-DESIGN.md に沿った ttk / ルートの配色。"""
        bg = self._design["bg"]
        surface = self._design["surface"]
        accent = self._design["accent"]
        accent_h = self._design["accent_hover"]
        fg = self._design["text_primary"]
        fg_muted = self._design["text_secondary"]
        self.configure(bg=bg)
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=bg)
        style.configure("TLabel", background=bg, foreground=fg, font=("Meiryo UI", 10))
        style.configure("TLabelframe", background=surface, foreground=fg, borderwidth=1, relief="solid")
        style.configure("TLabelframe.Label", background=surface, foreground=fg, font=("Meiryo UI", 10))
        style.configure("TButton", font=("Meiryo UI", 10))
        style.configure(
            "Accent.TButton",
            background=accent,
            foreground=self._design["white"],
            font=("Meiryo UI", 10),
            padding=(16, 8),
        )
        style.map(
            "Accent.TButton",
            background=[("active", accent_h), ("disabled", "#9CA3AF")],
            foreground=[("disabled", "#E5E7EB")],
        )
        style.configure("TEntry", fieldbackground=surface, foreground=fg, insertcolor=fg)
        style.configure("TCombobox", fieldbackground=surface, foreground=fg_muted)
        style.configure("TScrollbar", background=surface, troughcolor=bg, borderwidth=0)
        style.configure("Muted.TLabel", background=bg, foreground=fg_muted, font=("Meiryo UI", 9))
        style.configure("Section.TLabel", background=bg, foreground=fg, font=("Meiryo UI", 10, "bold"))

    def _make_toggle_section(
        self,
        parent,
        title: str,
        *,
        fill=tk.X,
        expand=False,
        pady=(0, 12),
        padding=(16, 12),
        initially_visible: bool = True,
        pack: bool = True,
    ) -> tuple[ttk.Frame, ttk.Frame, ttk.Label, Callable[[], None]]:
        section = ttk.Frame(parent)
        if pack:
            section.pack(fill=fill, expand=expand, pady=pady)

        header = ttk.Frame(section)
        header.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(header, text=title, style="Section.TLabel").pack(side=tk.LEFT)

        body = ttk.LabelFrame(section, padding=padding)
        visible = tk.BooleanVar(value=initially_visible)

        def _set_body_visibility() -> None:
            if visible.get():
                if not body.winfo_ismapped():
                    body.pack(fill=fill, expand=expand)
                toggle_label.configure(text="▲")
            else:
                body.pack_forget()
                toggle_label.configure(text="▼")
            self.after_idle(self._refresh_layout_after_toggle)

        def _toggle(_event=None) -> str:
            visible.set(not visible.get())
            _set_body_visibility()
            return "break"

        toggle_label = ttk.Label(header, text="▲", style="Section.TLabel", cursor="hand2")
        toggle_label.pack(side=tk.LEFT, padx=(8, 0))
        toggle_label.bind("<Button-1>", _toggle)
        toggle_label.bind("<Return>", _toggle)
        toggle_label.bind("<space>", _toggle)
        toggle_label.configure(takefocus=1)
        _set_body_visibility()
        return section, body, toggle_label, _toggle

    def _refresh_layout_after_toggle(self) -> None:
        self.update_idletasks()
        if self._results_canvas:
            self._results_canvas.configure(scrollregion=self._results_canvas.bbox("all"))

    def _build_ui(self):
        d = self._design
        main = ttk.Frame(self, padding=(24, 16))
        main.pack(fill=tk.BOTH, expand=True)

        # 動画ファイル + 参照 + 実行
        _, file_frame, _, _ = self._make_toggle_section(main, "動画ファイル", fill=tk.X, pady=(0, 6))

        row0 = ttk.Frame(file_frame)
        row0.pack(fill=tk.X)
        ttk.Entry(row0, textvariable=self.video_path_var, state="readonly").pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8)
        )
        ttk.Button(row0, text="参照", command=self._on_browse, style="Accent.TButton").pack(side=tk.LEFT, padx=(0, 4))
        self.analyze_btn = ttk.Button(
            row0, text="実行", command=self._on_analyze, state=tk.DISABLED, style="Accent.TButton"
        )
        self.analyze_btn.pack(side=tk.LEFT)

        # 画像抽出間隔
        interval_frame = ttk.Frame(file_frame)
        interval_frame.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(interval_frame, text="画像抽出間隔:").pack(side=tk.LEFT, padx=(0, 6))
        interval_combo = ttk.Combobox(
            interval_frame,
            textvariable=self._interval_var,
            values=["2", "5", "10", "30", "120"],
            width=4,
            state="readonly",
        )
        interval_combo.pack(side=tk.LEFT)
        ttk.Label(interval_frame, text="秒").pack(side=tk.LEFT, padx=(2, 0))
        ttk.Label(
            interval_frame,
            text="短いほど詳細に解析できますが、処理時間が長くなります。",
            style="Muted.TLabel",
        ).pack(side=tk.LEFT, padx=(12, 0))

        # 動画ファイル情報
        _, info_frame, _, _ = self._make_toggle_section(main, "動画ファイル情報", fill=tk.X, pady=(0, 12))
        ttk.Label(info_frame, textvariable=self._info_var, wraplength=600).pack(anchor=tk.W)

        # 解析状況: 長時間の Ollama 応答待ちをユーザーが把握できるよう常時表示する。
        _, self._progress_frame, _, _ = self._make_toggle_section(
            main, "解析状況", fill=tk.X, pady=(0, 12), padding=(16, 10)
        )
        progress_row = ttk.Frame(self._progress_frame)
        progress_row.pack(fill=tk.X)
        ttk.Label(
            progress_row,
            textvariable=self._progress_status_var,
            style="Section.TLabel",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._cancel_btn = ttk.Button(
            progress_row,
            text="キャンセル",
            command=self._on_cancel,
            state=tk.DISABLED,
        )
        self._cancel_btn.pack(side=tk.RIGHT, padx=(12, 0))
        self._progress_bar = ttk.Progressbar(
            self._progress_frame,
            orient=tk.HORIZONTAL,
            mode="determinate",
            maximum=100,
        )
        self._progress_bar.pack(fill=tk.X, pady=(8, 0))
        self._progress_status_var.set("待機中")

        # フレームごとの解析結果。総評は別ウィンドウ化し、この欄の表示面積を優先する。
        result_section, result_frame, _, _ = self._make_toggle_section(
            main,
            "フレームごとの解析結果",
            fill=tk.BOTH,
            expand=True,
            pady=(0, 8),
            padding=(16, 12),
        )

        result_toolbar = ttk.Frame(result_frame)
        result_toolbar.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(result_toolbar, text="フレーム一覧", style="Section.TLabel").pack(side=tk.LEFT)
        self._summary_window_btn = ttk.Button(
            result_toolbar,
            text="総評ウィンドウ",
            command=self._open_summary_window,
            state=tk.DISABLED,
            style="Accent.TButton",
        )
        self._summary_window_btn.pack(side=tk.RIGHT)
        ttk.Label(
            result_frame,
            text="横に並んだフレームは下の横スクロールバー、または Shift + マウスホイールで移動できます。",
            style="Muted.TLabel",
        ).pack(anchor=tk.W, pady=(0, 6))
        cards_container = ttk.Frame(result_frame)
        cards_container.pack(fill=tk.BOTH, expand=True)
        cards_container.columnconfigure(0, weight=1)
        cards_container.rowconfigure(0, weight=1)

        self._results_canvas = tk.Canvas(
            cards_container,
            bg=d["surface"],
            highlightthickness=1,
            highlightbackground=d["border"],
            highlightcolor=d["border"],
        )
        cards_vscroll = ttk.Scrollbar(cards_container, orient="vertical", command=self._results_canvas.yview)
        # grid で横スクロールバーを独立行に固定し、横並びカードでも常に操作できるようにする。
        self._results_hscroll = ttk.Scrollbar(
            cards_container,
            orient="horizontal",
            command=self._results_canvas.xview,
        )
        self._results_canvas.configure(
            xscrollcommand=self._results_hscroll.set,
            yscrollcommand=cards_vscroll.set,
        )
        self._results_canvas.grid(row=0, column=0, sticky="nsew")
        cards_vscroll.grid(row=0, column=1, sticky="ns")
        self._results_hscroll.grid(row=1, column=0, sticky="ew", pady=(6, 0))

        self._results_inner = ttk.Frame(self._results_canvas)
        self._results_win_id = self._results_canvas.create_window((0, 0), window=self._results_inner, anchor="nw")
        self._show_empty_state()

        def _on_cards_inner_configure(_e):
            if self._results_canvas:
                self._results_canvas.configure(scrollregion=self._results_canvas.bbox("all"))

        def _on_cards_canvas_configure(e):
            if self._results_canvas:
                # 内側フレームの高さをキャンバスに固定するとカード下部が見切れるため固定しない。
                # 必要なら縦スクロールで全体を見せる。
                self._results_canvas.configure(scrollregion=self._results_canvas.bbox("all"))

        self._results_inner.bind("<Configure>", _on_cards_inner_configure)
        self._results_canvas.bind("<Configure>", _on_cards_canvas_configure)
        self._results_canvas.bind("<Shift-MouseWheel>", self._on_shift_scroll)
        self._results_canvas.bind("<MouseWheel>", self._on_vertical_scroll)

        # 後方互換のため従来の参照を維持
        self._scroll_inner = self._results_inner
        self._scroll_canvas = self._results_canvas

        # ステータス
        status_frame = ttk.Frame(main)
        status_frame.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(status_frame, textvariable=self.status_var, style="Muted.TLabel").pack(anchor=tk.W)
        self.status_var.set("動画ファイルを選択し、実行を押してください")

    def _show_progress(self, msg: str, current: int, total: int):
        """状況エリアのメッセージとプログレスバーを更新"""
        self.status_var.set(msg)
        self._progress_status_var.set(msg)
        if self._progress_bar is not None:
            self._progress_bar.stop()
            if total > 0:
                self._progress_bar.configure(mode="determinate")
                self._progress_bar["value"] = min(100.0, max(0.0, current / total * 100))
            else:
                self._progress_bar.configure(mode="indeterminate")
                self._progress_bar.start(12)

    def _update_progress(self, msg: str, current: int, total: int):
        """状況メッセージとプログレスバーを更新"""
        self.status_var.set(msg)
        self._progress_status_var.set(msg)
        if self._progress_bar is not None and total > 0:
            self._progress_bar.stop()
            self._progress_bar.configure(mode="determinate")
            self._progress_bar["value"] = min(100.0, (current / total) * 100)

    def _hide_progress(self, message: str = "完了"):
        """解析終了後の状況表示（既定: 完了、エラー時は別メッセージ）"""
        self._progress_status_var.set(message)
        self.status_var.set(message)
        if self._cancel_btn is not None:
            self._cancel_btn.configure(state=tk.DISABLED)
        if self._progress_bar is not None and message == "完了":
            self._progress_bar.stop()
            self._progress_bar.configure(mode="determinate")
            self._progress_bar["value"] = 100
        elif self._progress_bar is not None:
            self._progress_bar.stop()
            self._progress_bar.configure(mode="determinate")

    def _on_shift_scroll(self, event):
        """Shift+マウスホイールで横スクロール（操作したキャンバスをスクロール）"""
        w = event.widget
        if isinstance(w, tk.Canvas):
            w.xview_scroll(int(-1 * (event.delta / 120)), "units")

    def _on_vertical_scroll(self, event):
        """マウスホイールで縦スクロール（画像キャンバス用。必要なら結果側も縦スクロール）"""
        w = event.widget
        if isinstance(w, tk.Canvas):
            w.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _on_cancel(self):
        self._cancel_requested = True
        self.status_var.set("キャンセル要求中です。現在のOllama処理が戻るまで待機します…")
        self._progress_status_var.set("キャンセル要求中です。現在のOllama処理が戻るまで待機します…")
        if self._cancel_btn is not None:
            self._cancel_btn.configure(state=tk.DISABLED)

    def _on_browse(self):
        path = filedialog.askopenfilename(
            title="動画を選択",
            filetypes=[
                ("動画", "*.mp4 *.webm *.avi *.mov *.mkv *.m4v"),
                ("すべて", "*.*"),
            ],
        )
        if path:
            self.video_path_var.set(path)
            self.analyze_btn.configure(state=tk.NORMAL)
            self.status_var.set(f"選択: {Path(path).name}")
            info = get_video_info(path)
            if info:
                dur = info["duration_sec"]
                m, s = int(dur // 60), int(dur % 60)
                self._info_var.set(
                    f"ファイル: {info['name']}  |  長さ: {m}分{s}秒  |  解像度: {info['width']}x{info['height']}  |  フレーム数: {info['frame_count']}"
                )
            else:
                self._info_var.set(f"ファイル: {Path(path).name}  （情報取得できませんでした）")

    def _get_config(self):
        """Ollama の設定を返す"""
        return {
            "ollama_base_url": os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
            "ollama_model": os.environ.get("OLLAMA_MODEL", "llava"),
            "ollama_japanese_model": os.environ.get("OLLAMA_JAPANESE_MODEL") or None,
            "ollama_summary_model": os.environ.get("OLLAMA_SUMMARY_MODEL") or None,
        }

    def _ensure_summary_window(self) -> tk.Toplevel:
        if self._summary_window is not None and self._summary_window.winfo_exists():
            self._summary_window.deiconify()
            self._summary_window.lift()
            return self._summary_window

        d = self._design
        win = tk.Toplevel(self)
        win.title("総評")
        win.geometry("720x560")
        win.minsize(520, 360)
        win.configure(bg=d["bg"])
        win.protocol("WM_DELETE_WINDOW", win.withdraw)
        self._summary_window = win

        main = ttk.Frame(win, padding=(16, 12))
        main.pack(fill=tk.BOTH, expand=True)

        summary_row = ttk.Frame(main)
        summary_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(summary_row, text="人格:").pack(side=tk.LEFT, padx=(0, 6))
        persona_combo = ttk.Combobox(
            summary_row,
            textvariable=self._summary_persona_var,
            values=[lbl for _, lbl in SUMMARY_PERSONA_CHOICES],
            state="readonly",
            width=22,
        )
        persona_combo.pack(side=tk.LEFT, padx=(0, 12))
        self._summary_btn = ttk.Button(
            summary_row,
            text="総評出力",
            command=self._on_summary,
            state=tk.NORMAL if self._frame_cards else tk.DISABLED,
            style="Accent.TButton",
        )
        self._summary_btn.pack(side=tk.LEFT)

        text_container = ttk.Frame(main)
        text_container.pack(fill=tk.BOTH, expand=True)
        vscroll = ttk.Scrollbar(text_container)
        vscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._summary_text = tk.Text(
            text_container,
            wrap=tk.WORD,
            height=18,
            font=("Meiryo UI", 10),
            cursor="arrow",
            yscrollcommand=vscroll.set,
            bg=d["surface"],
            fg=d["text_primary"],
            insertbackground=d["accent"],
            selectbackground=d["accent"],
            selectforeground=d["white"],
            relief="flat",
            borderwidth=1,
            highlightthickness=1,
            highlightbackground=d["border"],
            highlightcolor=d["accent"],
        )
        vscroll.configure(command=self._summary_text.yview)
        self._summary_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        _make_text_readonly(self._summary_text)
        self._summary_text.insert(tk.END, self._summary_placeholder_var.get())
        return win

    def _open_summary_window(self):
        self._ensure_summary_window()

    def _clear_cards(self):
        self._card_images = []
        self._frame_cards = []
        self._pending_frame_results = {}
        if self._summary_btn:
            self._summary_btn.configure(state=tk.DISABLED)
        if self._summary_text:
            self._summary_text.delete("1.0", tk.END)
        # カード一覧（画像+結果を同一エリアに統合）
        for inner, canvas in [(self._results_inner, self._results_canvas)]:
            if inner:
                for w in list(inner.winfo_children()):
                    w.destroy()
            if canvas:
                canvas.xview_moveto(0)
                canvas.yview_moveto(0)

    def _show_empty_state(self):
        if self._results_inner is None:
            return
        ttk.Label(
            self._results_inner,
            text="動画を選択して実行すると、抽出したフレームと解析結果がここに表示されます。",
            style="Muted.TLabel",
        ).pack(anchor=tk.W, padx=12, pady=16)

    def _build_cards(self, frames_jpeg: list[bytes]):
        """画像+解析結果を同一カードにまとめ、カードを横並びで作成する"""
        pending = dict(self._pending_frame_results)  # クリア前に退避
        self._clear_cards()
        if self._results_inner is None:
            return
        try:
            import numpy as np
        except Exception:
            np = None
        for idx, jpeg_bytes in enumerate(frames_jpeg):
            d = self._design
            card = ttk.Frame(self._results_inner)
            card.pack(side=tk.LEFT, padx=(0, 16), pady=8, fill=tk.Y, expand=False)

            # === 画像：サムネイル ===
            img = None
            if np is not None:
                try:
                    img = cv2.imdecode(np.frombuffer(jpeg_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
                except Exception:
                    pass
            # サムネイル表示サイズ（GUI 上の見た目の大きさ）を 2 倍にする
            thumb_h, thumb_w = 240, 400
            if img is not None:
                h, w = img.shape[:2]
                scale = min(thumb_w / max(1, w), thumb_h / max(1, h), 1.0)
                nw, nh = int(w * scale), int(h * scale)
                thumb = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
                ok, png_buf = cv2.imencode(".png", thumb)
                if ok:
                    b64 = base64.b64encode(png_buf.tobytes()).decode("ascii")
                    photo = tk.PhotoImage(data=b64)
                    self._card_images.append(photo)
                    ttk.Label(card, image=photo).pack(pady=(0, 6))
                else:
                    ttk.Label(card, text=f"フレーム{idx+1}\n(画像なし)", width=30, style="Muted.TLabel").pack(pady=(0, 6))
            else:
                ttk.Label(card, text=f"フレーム{idx+1}\n(表示できません)", width=30, style="Muted.TLabel").pack(pady=(0, 6))
            ttk.Label(card, text=f"フレーム{idx+1}", style="Muted.TLabel").pack(anchor=tk.W, pady=(0, 4))

            # === 解析結果：状態ラベル + テキスト（カード内スクロールは持たせない） ===
            label = ttk.Label(card, text=f"フレーム{idx+1} — 待機中")
            label.pack(anchor=tk.W, pady=(0, 2))
            # テキスト欄はカード内でスクロールさせず、カード一覧キャンバスのスクロールに統一する（入れ子スクロール回避）
            txt = tk.Text(
                card,
                wrap=tk.WORD,
                width=54,
                height=10,
                font=("Meiryo UI", 10),
                cursor="arrow",
                bg=d["surface"],
                fg=d["text_primary"],
                insertbackground=d["accent"],
                selectbackground=d["accent"],
                selectforeground=d["white"],
                relief="flat",
                borderwidth=1,
                highlightthickness=1,
                highlightbackground=d["border"],
                highlightcolor=d["accent"],
            )
            txt.pack(fill=tk.BOTH, expand=True)
            _make_text_readonly(txt)
            # マウスホイールはカード一覧キャンバスをスクロール（入れ子スクロールで混乱しないように）
            def _wheel_to_canvas(e, canvas=self._results_canvas):
                try:
                    if canvas:
                        canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
                except Exception:
                    pass
                return "break"
            def _shift_wheel_to_canvas(e, canvas=self._results_canvas):
                try:
                    if canvas:
                        canvas.xview_scroll(int(-1 * (e.delta / 120)), "units")
                except Exception:
                    pass
                return "break"
            txt.bind("<MouseWheel>", _wheel_to_canvas)
            txt.bind("<Shift-MouseWheel>", _shift_wheel_to_canvas)
            self._frame_cards.append({"label": label, "text": txt})
        # カード作成前に届いていた解析結果を反映（退避した分のみ）
        for idx, result in list(pending.items()):
            if idx < len(self._frame_cards):
                c = self._frame_cards[idx]
                state, body = _format_frame_result_for_ui(result)
                c["label"].configure(text=f"フレーム{idx+1} — {state}")
                t = c["text"]
                t.delete("1.0", tk.END)
                t.insert(tk.END, body)
        self._pending_frame_results.clear()
        if self._results_canvas:
            self._results_canvas.after(50, self._update_scroll_region)

    def _update_scroll_region(self):
        """両方のキャンバスのスクロール領域を内側フレームの実際のサイズに合わせる"""
        for canvas, inner in [(self._results_canvas, self._results_inner)]:
            if canvas and inner:
                canvas.update_idletasks()
                canvas.configure(scrollregion=canvas.bbox("all"))

    def _on_analyze(self):
        path_str = self.video_path_var.get().strip()
        if not path_str:
            self.status_var.set("動画ファイルを選択してください")
            return
        log_event("analyze_clicked", {"interval_sec": self._interval_var.get()})
        path = Path(path_str)
        if not path.exists() or not path.is_file():
            messagebox.showerror("エラー", "ファイルが見つかりません。")
            return
        config = self._get_config()

        self._cancel_requested = False
        self.analyze_btn.configure(state=tk.DISABLED)
        if self._cancel_btn is not None:
            self._cancel_btn.configure(state=tk.NORMAL)
        self._clear_cards()
        self.status_var.set("フレームを抽出しています…")
        self._show_progress("フレームを抽出しています…", 0, 0)
        self.update_idletasks()

        def on_progress(msg: str):
            self.after(0, lambda: self._show_progress(msg, 0, 0))

        def on_frame_start_index(current: int, total: int):
            def _update():
                self._update_progress(f"フレーム {current}/{total} を解析中…", current, total)
                # 各フレームの解析状態をラベルに表示（既に完了したフレームは on_frame_result のラベルを上書きしない）
                for i, c in enumerate(self._frame_cards):
                    n = i + 1
                    if n == current:
                        c["label"].configure(text=f"フレーム{n} — 解析中…")
                    elif n > current:
                        c["label"].configure(text=f"フレーム{n} — 待機中")

            self.after(0, _update)

        def on_frames_ready(frames: list):
            def _update():
                self._build_cards(frames)
                self._update_progress("解析を開始しています…", 0, len(frames))

            self.after(0, _update)

        def on_frame_intermediate(idx: int, result: str):
            """翻訳前（英語）の解析結果を一旦表示し、ラベルを「翻訳中…」にする"""
            def _update():
                if idx < len(self._frame_cards):
                    c = self._frame_cards[idx]
                    c["label"].configure(text=f"フレーム{idx+1} — 翻訳中…")
                    t = c["text"]
                    t.delete("1.0", tk.END)
                    inter = (result or "").strip()
                    shown = inter if inter else INTERMEDIATE_EMPTY_PLACEHOLDER
                    t.insert(tk.END, shown)
                    _set_text_height_to_content(t, shown)
                    log_event(
                        "frame_result_intermediate",
                        {
                            "frame_index": idx,
                            "len": len(inter),
                            "content": _truncate_for_log(inter),
                        },
                    )
                else:
                    # カード未作成なら最終結果と同様に保留
                    self._pending_frame_results[idx] = result or ""

            self.after(0, _update)

        def on_frame_result(idx: int, result: str):
            def _update():
                if idx < len(self._frame_cards):
                    c = self._frame_cards[idx]
                    state, body = _format_frame_result_for_ui(result)
                    c["label"].configure(text=f"フレーム{idx+1} — {state}")
                    t = c["text"]
                    t.delete("1.0", tk.END)
                    t.insert(tk.END, body)
                    _set_text_height_to_content(t, body)
                    # UIに実際に入っている文字を確認（「ログにはあるのに表示されない」切り分け用）
                    try:
                        ui_text = t.get("1.0", "end-1c")
                        log_event(
                            "ui_frame_text_widget_state",
                            {
                                "frame_index": idx,
                                "label_state": state,
                                "ui_len": len(ui_text),
                                "ui_head": _truncate_for_log(ui_text, limit=300),
                                "w": int(t.winfo_width() or 0),
                                "h": int(t.winfo_height() or 0),
                                "fg": str(t.cget("fg")),
                                "bg": str(t.cget("bg")),
                                "state_opt": str(t.cget("state")),
                            },
                        )
                    except Exception as _e:
                        log_event("ui_frame_text_widget_state_error", {"frame_index": idx, "msg": str(_e)[:200]})
                    # キャンバス側の描画・スクロール状態も記録
                    try:
                        canvas = self._results_canvas
                        if canvas:
                            canvas.update_idletasks()
                            log_event(
                                "ui_canvas_state",
                                {
                                    "frame_index": idx,
                                    "scrollregion": str(canvas.cget("scrollregion")),
                                    "xview": list(canvas.xview()),
                                    "yview": list(canvas.yview()),
                                    "canvas_w": int(canvas.winfo_width() or 0),
                                    "canvas_h": int(canvas.winfo_height() or 0),
                                    "text_xview": list(t.xview()),
                                    "text_yview": list(t.yview()),
                                },
                            )
                    except Exception as _e:
                        log_event("ui_canvas_state_error", {"frame_index": idx, "msg": str(_e)[:200]})
                    if state == "完了（本文なし）":
                        log_event(
                            "frame_result_empty",
                            {
                                "frame_index": idx,
                                "label_state": state,
                                "raw_len": len((result or "").strip()),
                            },
                        )
                    elif state == "エラー":
                        log_event(
                            "frame_result_error",
                            {
                                "frame_index": idx,
                                "label_state": state,
                                "msg_head": ((result or "")[:200]),
                            },
                        )
                    else:
                        log_event(
                            "frame_result_done",
                            {
                                "frame_index": idx,
                                "label_state": state,
                                "len": len((body or "").strip()),
                                "content": _truncate_for_log((body or "").strip()),
                            },
                        )
                else:
                    # カードがまだ作成されていない場合（レース条件）は保留して後で反映
                    self._pending_frame_results[idx] = result or ""

            self.after(0, _update)

        def on_complete(total: int, error_count: int, cancelled: bool):
            def _update():
                self._on_analysis_complete(total, error_count, cancelled)

            self.after(0, _update)

        def on_error(msg: str):
            def _update():
                self._hide_progress("解析に失敗しました")
                self._on_analysis_error(msg)

            self.after(0, _update)

        def work():
            try:
                interval = float(self._interval_var.get())
            except (ValueError, TypeError):
                interval = 10.0
            run_analysis_per_frame(
                path,
                config,
                interval,
                on_progress,
                on_frames_ready,
                on_frame_start_index,
                 on_frame_intermediate,
                on_frame_result,
                on_complete,
                on_error,
                lambda: self._cancel_requested,
            )

        thread = threading.Thread(target=work, daemon=True)
        thread.start()

    def _on_analysis_complete(self, total: int, error_count: int = 0, cancelled: bool = False):
        if cancelled:
            message = f"キャンセルしました（{total} フレーム中 {error_count} 件エラー）"
        elif error_count:
            message = f"一部失敗（{total} フレーム中 {error_count} 件エラー）"
        else:
            message = f"完了（{total} フレーム解析）"
        self._hide_progress(message)
        self.analyze_btn.configure(state=tk.NORMAL)
        if self._summary_window_btn:
            self._summary_window_btn.configure(state=tk.NORMAL if not cancelled else tk.DISABLED)
        if self._summary_btn:
            self._summary_btn.configure(state=tk.NORMAL if not cancelled else tk.DISABLED)
        if error_count and not cancelled:
            messagebox.showwarning("解析は一部失敗しました", "一部フレームの解析に失敗しました。各フレームのエラー内容を確認してください。")

    def _on_analysis_error(self, msg: str):
        self._hide_progress("解析に失敗しました")
        self.analyze_btn.configure(state=tk.NORMAL)
        messagebox.showerror("解析エラー", msg)

    def _on_summary(self):
        """全フレームの解析結果をAIに送り、総評を生成して表示する"""
        if not self._frame_cards:
            messagebox.showinfo("総評", "解析結果がありません。先に実行でフレーム解析を完了してください。")
            return
        config = self._get_config()
        # 総評用モデル: OLLAMA_SUMMARY_MODEL が設定されていればそれを使用、未設定なら OLLAMA_MODEL、それもなければ llama3.2
        model = config.get("ollama_summary_model") or config.get("ollama_model") or "llama3.2"
        base_url = config.get("ollama_base_url", "http://localhost:11434")

        results: list[str] = []
        for c in self._frame_cards:
            t = c["text"]
            results.append(t.get("1.0", tk.END).strip())
        if not any(r for r in results):
            messagebox.showinfo("総評", "解析結果テキストが空です。")
            return

        persona_label = self._summary_persona_var.get()
        persona_key = _SUMMARY_LABEL_TO_KEY.get(persona_label, "neutral")

        if self._summary_btn:
            self._summary_btn.configure(state=tk.DISABLED)
        if self._summary_text:
            self._summary_text.delete("1.0", tk.END)
            self._summary_text.insert(tk.END, "総評を生成中…")
        self._progress_status_var.set("総評を生成中…")
        self.status_var.set("総評を生成中…")

        def work():
            try:
                summary = get_summary_from_frame_results(
                    results,
                    base_url=base_url,
                    model=model,
                    persona_key=persona_key,
                )
                def _done():
                    if self._summary_text:
                        self._summary_text.delete("1.0", tk.END)
                        self._summary_text.insert(tk.END, summary)
                    if self._summary_btn:
                        self._summary_btn.configure(state=tk.NORMAL)
                    self._progress_status_var.set("完了")
                    self.status_var.set("総評を出力しました")
                    log_event(
                        "summary_done",
                        {
                            "model": model,
                            "persona_key": persona_key,
                            "summary_len": len((summary or "").strip()),
                            "frame_count": len(results),
                            "content": _truncate_for_log((summary or "").strip(), limit=4000),
                        },
                    )
                self.after(0, _done)
            except Exception as e:
                def _err():
                    if self._summary_text:
                        self._summary_text.delete("1.0", tk.END)
                        self._summary_text.insert(tk.END, f"エラー: {e}")
                    if self._summary_btn:
                        self._summary_btn.configure(state=tk.NORMAL)
                    self._progress_status_var.set("総評に失敗")
                    self.status_var.set("総評の生成に失敗しました")
                    messagebox.showerror("総評エラー", str(e))
                    log_event(
                        "summary_error",
                        {
                            "model": model,
                            "persona_key": persona_key,
                            "msg": (str(e)[:300]),
                            "frame_count": len(results),
                        },
                    )
                self.after(0, _err)

        threading.Thread(target=work, daemon=True).start()


def main():
    app = VideoAnalyzerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
