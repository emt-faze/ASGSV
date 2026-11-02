"""Software playback timings, separate from acoustic latency or subtitle onset."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import statistics
from pathlib import Path
from uuid import uuid4

STAGES = (("recognition_ms", "识别与传递"), ("confirmation_ms", "匹配与稳定确认"),
          ("queue_ms", "播放队列等待"), ("player_start_ms", "文件检查与播放器启动"), ("total_ms", "合计"))


def timing_sample(seed, dequeued, started):
    if not seed:
        return None
    automatic = seed["trigger"] == "automatic"
    capture = seed.get("capture") if automatic else seed["queued"]
    received = seed.get("received") if automatic else seed["queued"]
    points = (capture, received, seed["queued"], dequeued, started)
    if not all(isinstance(value, (float, int)) and math.isfinite(value) for value in points):
        return None
    if any(right < left for left, right in zip(points, points[1:])):
        return None
    values = [round((right-left)*1000, 3) for left, right in zip(points, points[1:])]
    result = dict(zip((key for key, _ in STAGES[:-1]), values))
    result.update(total_ms=round((started-capture)*1000, 3), trigger=seed["trigger"],
                  match_kind=seed["match_kind"],
                  origin="first_matching_capture" if automatic else "manual_request",
                  endpoint="player_started_callback")
    return result


def percentile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    if fraction == .5:
        return statistics.median(values)
    return values[max(0, math.ceil(len(values)*fraction)-1)]


class PlaybackMetrics:
    def __init__(self, project_directory):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
        self.path = Path(project_directory) / "playback-statistics" / (stamp + ".jsonl")
        self.samples, self.seen = [], set()
        self.save_error = ""
        self._summary_attempt_count = 0

    def add(self, event):
        sample = event.get("latency")
        if event.get("state") != "playing" or not sample or event["generation"] in self.seen:
            return False
        self.seen.add(event["generation"])
        entry = dict(sample, row_id=event.get("row_id"), text=event.get("text", ""), voice=event.get("voice", ""),
                     generation=event["generation"], recorded_at=datetime.now(timezone.utc).isoformat())
        self.samples.append(entry)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, ensure_ascii=False, allow_nan=False) + "\n")
        except OSError as exc:
            self.save_error = "统计记录未能保存：" + str(exc)
        return True

    def summary(self):
        automatic = [sample for sample in self.samples if sample["trigger"] == "automatic"]
        stages = {key: dict(p50=percentile([s[key] for s in automatic], .5),
                            p95=percentile([s[key] for s in automatic], .95),
                            maximum=max((s[key] for s in automatic), default=None)) for key, _ in STAGES}
        return dict(automatic_count=len(automatic), manual_count=len(self.samples)-len(automatic), stages=stages,
                    slowest=sorted(automatic, key=lambda item: item["total_ms"], reverse=True)[:5],
                    definition="首次可匹配截图至播放器启动回调；只统计成功启动的自动播放；不等于扬声器首音延迟。")

    def label(self):
        summary = self.summary()
        if not summary["automatic_count"]:
            return f"延迟统计：尚无自动播放样本 · 手动播放 {summary['manual_count']} 次"
        stage = summary["stages"]["total_ms"]
        return (f"自动播放 {summary['automatic_count']} 次 · 中位 {stage['p50']/1000:.2f} 秒 · "
                f"p95 {stage['p95']/1000:.2f} 秒 · 手动播放 {summary['manual_count']} 次（另计）")

    def save_summary(self):
        if not self.samples or self._summary_attempt_count == len(self.samples):
            return
        self._summary_attempt_count = len(self.samples)
        from dialogue_generation import atomic_text
        try:
            atomic_text(self.path.with_suffix(".summary.json"), json.dumps(self.summary(), ensure_ascii=False, indent=2))
        except OSError as exc:
            self.save_error = "统计汇总未能保存：" + str(exc)


def show_metrics(parent, metrics):
    from PySide6 import QtCore, QtGui, QtWidgets
    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle("本次播放延迟统计")
    dialog.resize(920, 570)
    layout = QtWidgets.QVBoxLayout(dialog)
    summary = metrics.summary()
    label = QtWidgets.QLabel(metrics.label() + "\n" + summary["definition"] +
                            "\n不包含首次可匹配截图之前的字幕打字等待。样本较少时，p95 只能描述本次样本。")
    label.setWordWrap(True)
    layout.addWidget(label)
    table = QtWidgets.QTableWidget(len(STAGES), 4)
    table.setHorizontalHeaderLabels(["阶段", "中位数（毫秒）", "p95（毫秒）", "最慢（毫秒）"])
    table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
    table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
    for row, (key, title) in enumerate(STAGES):
        values = [title] + ["—" if summary["stages"][key][field] is None else f"{summary['stages'][key][field]:.1f}"
                            for field in ("p50", "p95", "maximum")]
        for col, value in enumerate(values):
            table.setItem(row, col, QtWidgets.QTableWidgetItem(value))
    layout.addWidget(table)
    slow = QtWidgets.QPlainTextEdit()
    slow.setReadOnly(True)
    slow.setPlainText("最慢的自动播放：\n" + "\n".join(
        f"{s['total_ms']/1000:.2f} 秒 · {s['voice']} · {s['text']}" for s in summary["slowest"]))
    layout.addWidget(slow)
    metrics.save_summary()
    path_label = QtWidgets.QLabel(metrics.save_error or ("逐句记录：" + str(metrics.path)))
    path_label.setWordWrap(True)
    path_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
    layout.addWidget(path_label)
    buttons = QtWidgets.QDialogButtonBox()
    buttons.addButton("关闭", QtWidgets.QDialogButtonBox.RejectRole)
    folder = buttons.addButton("打开统计文件夹", QtWidgets.QDialogButtonBox.ActionRole)
    folder.setEnabled(metrics.path.parent.is_dir())
    folder.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(metrics.path.parent))))
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.exec()
