"""Explain and review generator voice routing without changing live dubbing."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class VoiceDecision:
    label: str
    detail: str
    profile: object = None
    fallback: bool = False
    needs_review: bool = False
    candidates: list[tuple[str, str]] = field(default_factory=list)


def describe_voice(runtime, row):
    from dialogue_generation import select_voice
    from wuwa_dubber.speaker_matcher import SpeakerMatcher
    from wuwa_dubber.text_processing import normalize_speaker

    try:
        profile, fallback = select_voice(runtime, row)
    except ValueError as exc:
        return VoiceDecision("声线已失效", str(exc), needs_review=True)
    if row.voice != "auto":
        return VoiceDecision("手动指定", "使用本条台词明确指定的声线。", profile, fallback)

    record = row.route_record()
    name = (record.get("speaker", "") if record.get("speaker_status") == "verified" else
            record.get("speaker_identity", "") if record.get("speaker_identity_confirmed") else "")
    profiles = list(runtime.profiles.values())
    threshold = getattr(runtime.config, "speaker_match_threshold", .82)
    matches = []
    for candidate in profiles:
        match = SpeakerMatcher([candidate], threshold=threshold).match(name or row.speaker)
        if match:
            matches.append(match)
    matches.sort(key=lambda item: (-item.score, item.speaker))
    exact = [match for match in matches if match.score == 1]
    narrator = runtime.profiles.get(getattr(runtime.config, "narrator_profile", ""))
    default_names = {normalize_speaker(n) for n in ("旁白", "叙述者", "通用旁白")}
    if narrator:
        default_names.update(normalize_speaker(n) for n in [narrator.speaker, *narrator.aliases])
    intentional_default = not row.speaker.strip() or normalize_speaker(name) in default_names

    if runtime.system_mode:
        router = getattr(runtime, "system_router", None)
        if router is None:  # Runtimes with their own public selection implementation.
            return VoiceDecision("默认声线" if fallback else "角色绑定", "使用当前系统声线设置。", profile, fallback)
        from system_voices import PRESETS, preset_profile
        candidates = [("system:" + key, label) for key, (label, *_) in PRESETS.items()
                      if preset_profile(key) == profile]
        if len(exact) > 1:
            return VoiceDecision("别名冲突", "这个名字属于多个角色，请明确指定声音。", profile, fallback, True, candidates)
        if row.speaker and not name:
            return VoiceDecision("角色待核对", "角色身份尚未确认，目前将使用默认声音。", profile, fallback, True, candidates)
        if fallback:
            return VoiceDecision("默认声线", "未设置专属系统声音。" if not intentional_default else "按设置使用默认声音。",
                                 profile, True, not intentional_default, candidates)
        key = normalize_speaker(name)
        alias = router.aliases.get(key, key) != key
        return VoiceDecision("别名命中" if alias else "角色名命中", f"按系统角色绑定选择：{name}。", profile)

    candidates = [("character:" + item.speaker,
                   f"{item.speaker}（{'名字一致' if item.score == 1 else '相似度 ' + format(item.score, '.0%')}）")
                  for item in matches]
    if row.speaker and not name:
        return VoiceDecision("角色待核对", "角色身份尚未确认，请核对角色或明确指定声线。", profile, fallback, True, candidates)
    if len(exact) > 1:
        return VoiceDecision("别名冲突", "这个名字属于多个角色，请明确选择。", profile, fallback, True, candidates)
    if exact:
        match = exact[0]
        alias = normalize_speaker(name) != normalize_speaker(match.speaker)
        return VoiceDecision("别名命中" if alias else "角色名命中",
                             f"{name} → {match.speaker}" + (f"；已登记别名：{match.matched_alias}" if alias else ""), profile)
    if matches:
        return VoiceDecision("模糊候选", "角色名未精确匹配。候选仅供选择，确认后再生成。", profile, fallback, True, candidates)
    return VoiceDecision("默认声线", "未找到对应角色声线。" if not intentional_default else "按设置使用默认声线。",
                         profile, fallback, not intentional_default,
                         [("character:" + profile.speaker, profile.speaker + "（默认声线）")])


def uncertain_rows(runtime, rows):
    return [(row, decision) for row in rows if not row.punctuation_only
            and (decision := describe_voice(runtime, row)).needs_review]


def apply_voice_choices(project, choices):
    """Apply explicit per-row routing; text confirmation remains independent."""
    changed = 0
    for row in project.rows:
        voice = choices.get(row.id)
        if voice and voice != row.voice:
            row.edit(voice=voice)
            changed += 1
    if changed:
        project.save()
    return changed


def review_dialog(parent, issues, voices):
    from PySide6 import QtCore, QtWidgets
    from generator_ui_support import configure_dialog

    class ReviewDialog(QtWidgets.QDialog):
        def __init__(self):
            super().__init__(parent)
            self.setWindowTitle("集中核对角色声线")
            self.resize(900, 470)
            self.choices = {}
            layout = QtWidgets.QVBoxLayout(self)
            note = QtWidgets.QLabel("同一角色的相同问题合并显示。选择后应用到本组台词；暂不处理的条目会保留，生成时跳过。\n"
                                   "这里确认声线，台词文字仍沿用原来的核对状态。")
            note.setWordWrap(True)
            layout.addWidget(note)
            groups = {}
            for row, decision in issues:
                key = (row.speaker, decision.label, decision.detail, tuple(decision.candidates))
                groups.setdefault(key, []).append(row)
            self.table = QtWidgets.QTableWidget(len(groups), 4)
            self.table.setHorizontalHeaderLabels(["角色 / 条数", "需要核对的原因", "台词示例", "确认使用的声线"])
            self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            self.table.verticalHeader().setDefaultSectionSize(48)
            self.table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
            for col, width in ((0, 130), (1, 145), (3, 260)):
                self.table.setColumnWidth(col, width)
            self.groups = []
            for index, ((speaker, label, detail, candidates), rows) in enumerate(groups.items()):
                for col, value in enumerate((f"{speaker or '未指定'} · {len(rows)} 条", label, rows[0].text)):
                    item = QtWidgets.QTableWidgetItem(value)
                    item.setToolTip(detail if col == 1 else "\n".join(row.text for row in rows) if col == 2 else value)
                    self.table.setItem(index, col, item)
                combo = QtWidgets.QComboBox()
                combo.addItem("暂不处理", "")
                seen = set()
                for identity, title in candidates:
                    combo.addItem("候选：" + title, identity)
                    seen.add(identity)
                for title, identity in voices:
                    if identity != "auto" and identity not in seen:
                        combo.addItem(title, identity)
                        seen.add(identity)
                self.table.setCellWidget(index, 3, combo)
                self.groups.append((rows, combo))
            layout.addWidget(self.table)
            buttons = QtWidgets.QDialogButtonBox()
            buttons.addButton("保存已选声线", QtWidgets.QDialogButtonBox.AcceptRole)
            buttons.addButton("取消", QtWidgets.QDialogButtonBox.RejectRole)
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)
            configure_dialog(self, 'review')

        def accept(self):
            self.choices = {row.id: combo.currentData() for rows, combo in self.groups
                            if combo.currentData() for row in rows}
            super().accept()

    return ReviewDialog()
