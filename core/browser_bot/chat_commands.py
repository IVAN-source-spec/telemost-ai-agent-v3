import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from core.browser_bot.participants_summary import ParticipantsSummaryBuilder


class ChatCommandsModule:
    ADD_AGENDA_COMMAND = "#добавить повестку"
    COMMAND_DESCRIPTION_COMMANDS = ("#\u043e\u043f\u0438\u0441\u0430\u043d\u0438\u0435 \u043a\u043e\u043c\u0430\u043d\u0434",)
    EXIT_BOT_COMMANDS = ("#\u0432\u044b\u0445\u043e\u0434 \u0431\u043e\u0442\u0430",)
    DELETE_PARTICIPANTS_PREFIXES = ("#\u0443\u0434\u0430\u043b\u0438\u0442\u044c \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u043e\u0432",)
    ADD_EXPECTED_PARTICIPANTS_PREFIXES = ("#\u0434\u043e\u0431\u0430\u0432\u0438\u0442\u044c \u043f\u0440\u0435\u0434\u043f\u043e\u043b\u0430\u0433\u0430\u0435\u043c\u043e\u0433\u043e \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u0430",)
    REMOVE_EXPECTED_PARTICIPANTS_PREFIXES = ("#\u0443\u0434\u0430\u043b\u0438\u0442\u044c \u043f\u0440\u0435\u0434\u043f\u043e\u043b\u0430\u0433\u0430\u0435\u043c\u043e\u0433\u043e \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u0430",)
    CLEAR_EXPECTED_PARTICIPANT_EMAIL_PREFIXES = ("#\u0443\u0434\u0430\u043b\u0435\u043d\u0438\u0435 \u043f\u043e\u0447\u0442\u044b \u043f\u0440\u0435\u0434\u043f\u043e\u043b\u0430\u0433\u0430\u0435\u043c\u043e\u0433\u043e \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u0430",)
    CHANGE_EXPECTED_PARTICIPANT_EMAIL_PREFIXES = ("#\u0438\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u0435 \u043f\u043e\u0447\u0442\u044b \u043f\u0440\u0435\u0434\u043f\u043e\u043b\u0430\u0433\u0430\u0435\u043c\u043e\u0433\u043e \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u0430",)
    LIST_EXPECTED_PARTICIPANTS_COMMANDS = ("#\u043f\u0440\u0435\u0434\u043f\u043e\u043b\u0430\u0433\u0430\u0435\u043c\u044b\u0435 \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u0438",)
    NEXT_AGENDA_COMMANDS = ("#\u0441\u043b\u0435\u0434\u0443\u044e\u0449\u0438\u0439 \u0432\u043e\u043f\u0440\u043e\u0441",)
    END_AGENDA_QUESTION_COMMANDS = ("#конец вопроса",)
    AGENDA_WITHOUT_TIME_COMMANDS = ("#вопросы без указания времени",)
    UNFINISHED_AGENDA_COMMANDS = ("#незавершенные вопросы",)
    ALL_AGENDA_COMMANDS = ("#все вопросы",)
    SKIP_CURRENT_AGENDA_COMMANDS = ("#пропустить текущий вопрос",)
    SWITCH_AGENDA_QUESTION_RE = re.compile(r"^#\s*вопрос(?:\s*№|\s+номер)?\s*(\d+)\s*$", re.IGNORECASE)
    ASSIGN_AGENDA_TIME_RE = re.compile(r"^#\s*\u043d\u0430\u0437\u043d\u0430\u0447\u0438\u0442\u044c\s+\u0432\u0440\u0435\u043c\u044f\s+\u2116?\s*(\d+)\s*(?:[-\u2014]\s*)?([0-9]+(?::[0-9]{1,2}){0,2})\s*$", re.IGNORECASE)
    ADD_AGENDA_QUESTION_RE = re.compile(r"^#\s*добавить\s+вопрос\s+(.+?)\s*$", re.IGNORECASE)
    SKIP_AGENDA_QUESTION_RE = re.compile(r"^#\s*пропустить\s+вопрос\s+№\s*(\d+)\s*$", re.IGNORECASE)
    CONFIDENTIAL_NO_RECORDING_PREFIXES = (
        "#\u043a\u043e\u043d\u0444\u0438\u0434\u0435\u043d\u0446\u0438\u0430\u043b\u044c\u043d\u043e \u0431\u0435\u0437 \u0437\u0430\u043f\u0438\u0441\u0438 \u0434\u043b\u044f",
        "#\u043a\u043e\u043d\u0444\u0435\u0434\u0435\u043d\u0446\u0438\u0430\u043b\u044c\u043d\u043e \u0431\u0435\u0437 \u0437\u0430\u043f\u0438\u0441\u0438 \u0434\u043b\u044f",
    )
    CONFIDENTIAL_PREFIXES = (
        "#\u043a\u043e\u043d\u0444\u0438\u0434\u0435\u043d\u0446\u0438\u0430\u043b\u044c\u043d\u043e \u0434\u043b\u044f",
        "#\u043a\u043e\u043d\u0444\u0435\u0434\u0435\u043d\u0446\u0438\u0430\u043b\u044c\u043d\u043e \u0434\u043b\u044f",
    )

    def __init__(self, page, logger=print, bot_id: str | None = None, confidential_event_handler=None, agenda_event_handler=None, agenda_enabled: bool = False, expected_participants=None, expected_participants_event_handler=None):
        self.page = page
        self.logger = logger
        self.bot_id = bot_id or "unknown"
        self.confidential_event_handler = confidential_event_handler
        self.agenda_event_handler = agenda_event_handler
        self.agenda_enabled = bool(agenda_enabled)
        self.expected_participants_event_handler = expected_participants_event_handler
        self._expected_participants = self._parse_expected_participant_items(expected_participants)
        self._seen_message_keys: set[str] = set()
        self._handled_command_keys: set[str] = set()
        self._monitor_task: asyncio.Task | None = None
        self._bot_sent_texts: set[str] = set()
        self._startup_anchor_text = str()
        self._startup_anchor_sent = False
        self._startup_anchor_seen = False
        self._startup_anchor_wait_scans = 0
        self._confidential_mode: str | None = None
        self._agenda_submission_author: str | None = None
        self._agenda_submission_deadline: float | None = None
        self._agenda_submission_timeout_seconds = int(
            os.getenv("TELEMOST_CHAT_AGENDA_SUBMISSION_TIMEOUT_SECONDS", "120")
        )
        self._recent_command_times: dict[str, float] = {}
        self._command_dedup_seconds = max(
            0.0,
            float(os.getenv("TELEMOST_COMMAND_DEDUP_SECONDS", "5")),
        )
        self._recent_service_message_times: dict[str, float] = {}
        self._outgoing_message_dedup_seconds = max(
            0.0,
            float(os.getenv("TELEMOST_OUTGOING_MESSAGE_DEDUP_SECONDS", "20")),
        )

    async def run_probe(self) -> Path:
        self.logger("[Bot] Chat commands module enabled")
        self._messages_path().touch(exist_ok=True)

        click_result = await self._click_chat_button()
        self.logger(f"[Bot] Chat button result: {click_result}")

        await self._capture_existing_messages_baseline()
        await self._send_startup_message()
        self._start_message_monitor()
        return self._messages_path()


    async def handle_command_text(
        self,
        text: str,
        *,
        source: str = "external",
        command_id: str | None = None,
    ) -> bool:
        normalized_text = self._normalize_message_text(str(text or ""))
        if not normalized_text:
            return False
        key_value = command_id or normalized_text
        command_key = f"{source}:{key_value}"
        before_count = len(self._handled_command_keys)
        await self._handle_new_messages([
            {
                "text": normalized_text,
                "_message_key": command_key,
                "author": source,
                "time": "",
                "_external_source": source,
            }
        ])
        return command_key in self._handled_command_keys or len(self._handled_command_keys) > before_count

    def _is_duplicate_command(self, text: str, source: str) -> bool:
        normalized = self._normalize_message_text(text).casefold()
        if not normalized.startswith("#") or self._command_dedup_seconds <= 0:
            return False

        now = time.monotonic()
        last_seen = self._recent_command_times.get(normalized)
        duplicate = (
            last_seen is not None
            and now - last_seen < self._command_dedup_seconds
        )
        if not duplicate:
            self._recent_command_times[normalized] = now
        for command, seen_at in list(self._recent_command_times.items()):
            if now - seen_at > self._command_dedup_seconds * 4:
                self._recent_command_times.pop(command, None)

        if duplicate:
            self.logger(
                f"[Bot] Duplicate command suppressed across sources: "
                f"{normalized!r} from {source}; "
                f"{now - last_seen:.1f}s since previous input"
            )
        return duplicate

    async def _capture_existing_messages_baseline(self) -> None:
        try:
            await self.page.wait_for_timeout(800)
            messages = await self._read_visible_chat_messages()
            ignored = 0
            for message in messages:
                if self._is_own_service_message(message):
                    continue
                key = self._message_key(message)
                self._seen_message_keys.add(key)
                self._handled_command_keys.add(key)
                ignored += 1
            self.logger(f"[Bot] Chat command baseline captured: {ignored} existing message(s) ignored")
        except Exception as error:
            self.logger(f"[Bot] Chat command baseline failed: {error}")

    async def _click_chat_button(self) -> str:
        result = await self.page.evaluate("""() => {
            const blacklist = ['demonstrac', 'screen', 'share', 'presentation'];

            const isVisibleAndEnabled = (element) => {
                const rect = element.getBoundingClientRect();
                const style = window.getComputedStyle(element);
                return rect.width > 0 && rect.height > 0 &&
                    style.display !== 'none' &&
                    style.visibility !== 'hidden' &&
                    !element.disabled;
            };

            const getLabels = (element) => {
                const text = (element.innerText || element.textContent || '').trim();
                const ariaLabel = (element.getAttribute('aria-label') || '').trim();
                const title = (element.getAttribute('title') || '').trim();
                const testId = (element.getAttribute('data-testid') || '').trim();
                const combined = `${text} ${ariaLabel} ${title} ${testId}`.toLowerCase();
                return { text, ariaLabel, title, testId, combined };
            };

            const isChatControl = (element) => {
                const { text, ariaLabel, title, combined } = getLabels(element);
                if (blacklist.some((word) => combined.includes(word))) {
                    return false;
                }
                return (
                    text.toLowerCase() === '\u0447\u0430\u0442' ||
                    ariaLabel.toLowerCase() === '\u0447\u0430\u0442' ||
                    ariaLabel.toLowerCase() === '\u043e\u0442\u043a\u0440\u044b\u0442\u044c \u0447\u0430\u0442' ||
                    title.toLowerCase() === '\u0447\u0430\u0442' ||
                    title.toLowerCase() === '\u043e\u0442\u043a\u0440\u044b\u0442\u044c \u0447\u0430\u0442'
                );
            };

            const buttons = Array.from(document.querySelectorAll('button, [role="button"]'));
            const chatButton = buttons.find((button) => isVisibleAndEnabled(button) && isChatControl(button));
            if (!chatButton) {
                return 'chat button not found';
            }

            const { text, ariaLabel, title, testId } = getLabels(chatButton);
            chatButton.click();
            return `clicked via exact text: ${ariaLabel || title || testId || text || 'unknown'}`;
        }""")
        return result

    async def _send_startup_message(self) -> None:
        message = self._startup_message_text()
        self._startup_anchor_text = self._normalize_message_text(message)
        if not message:
            self._startup_anchor_seen = True
            return

        try:
            before_count = await self._message_text_occurrence_count(message)
            last_result = "not attempted"
            for attempt in range(1, 4):
                send_result = await self._send_service_message(
                    message,
                    deduplicate=False,
                    suppress_unverified_retry=False,
                )
                last_result = send_result
                await self.page.wait_for_timeout(1200)
                after_count = await self._message_text_occurrence_count(message)
                self.logger(
                    f"[Bot] Chat startup message result: {send_result}; "
                    f"visible matches {before_count}->{after_count}; attempt {attempt}/3"
                )
                if after_count > before_count or await self._latest_visible_message_matches(message):
                    self._startup_anchor_sent = True
                    return
                await self.page.wait_for_timeout(1500)
            self._startup_anchor_sent = False
            await self._capture_existing_messages_baseline()
            self._startup_anchor_seen = False
            self.logger(
                f"[Bot] Chat command session anchor is waiting because startup message was not visible; "
                f"last result: {last_result}"
            )
        except Exception as error:
            self.logger(f"[Bot] Chat startup message failed: {error}")
            await self._capture_existing_messages_baseline()
            self._startup_anchor_seen = False
            self.logger("[Bot] Chat command session anchor is waiting after startup message failure")

    async def _message_text_occurrence_count(self, message: str) -> int:
        expected = self._normalize_message_text(message)
        try:
            messages = await self._read_visible_chat_messages()
        except Exception:
            return 0
        count = 0
        for item in messages:
            text = self._normalize_message_text(str(item.get("text") or ""))
            if text == expected:
                count += 1
        return count

    async def _latest_visible_message_matches(self, message: str) -> bool:
        expected = self._normalize_message_text(message)
        if not expected:
            return False
        try:
            messages = await self._read_visible_chat_messages()
        except Exception:
            return False
        for item in reversed(messages):
            text = self._normalize_message_text(str(item.get("text") or ""))
            if text:
                return text == expected
        return False

    async def _visible_message_match_count(self, message: str) -> int:
        return await self._message_text_occurrence_count(message)

    def _startup_message_text(self) -> str:
        command_lines = [
            "Бот подключен.",
            "Доступные команды:",
            "#описание команд",
            "#конфиденциально для",
            "#конфиденциально без записи для",
            "#выход бота",
        ]
        if self.agenda_enabled:
            command_lines.extend([
                "#следующий вопрос",
                "#вопрос №",
                "#конец вопроса",
                "#вопросы без указания времени",
                "#незавершенные вопросы",
                "#все вопросы",
                "#назначить время №",
                "#добавить вопрос",
                "#пропустить текущий вопрос",
                "#пропустить вопрос №",
            ])
        else:
            command_lines.append(self.ADD_AGENDA_COMMAND)
        default_message = "\n".join(command_lines)
        message = os.getenv("TELEMOST_CHAT_COMMANDS_STARTUP_MESSAGE", default_message).strip()
        extra_commands = [
            "#\u0443\u0434\u0430\u043b\u0438\u0442\u044c \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u043e\u0432",
            "#\u043f\u0440\u0435\u0434\u043f\u043e\u043b\u0430\u0433\u0430\u0435\u043c\u044b\u0435 \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u0438",
            "#\u0434\u043e\u0431\u0430\u0432\u0438\u0442\u044c \u043f\u0440\u0435\u0434\u043f\u043e\u043b\u0430\u0433\u0430\u0435\u043c\u043e\u0433\u043e \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u0430",
            "#\u0443\u0434\u0430\u043b\u0438\u0442\u044c \u043f\u0440\u0435\u0434\u043f\u043e\u043b\u0430\u0433\u0430\u0435\u043c\u043e\u0433\u043e \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u0430",
            "#\u0443\u0434\u0430\u043b\u0435\u043d\u0438\u0435 \u043f\u043e\u0447\u0442\u044b \u043f\u0440\u0435\u0434\u043f\u043e\u043b\u0430\u0433\u0430\u0435\u043c\u043e\u0433\u043e \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u0430",
            "#\u0438\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u0435 \u043f\u043e\u0447\u0442\u044b \u043f\u0440\u0435\u0434\u043f\u043e\u043b\u0430\u0433\u0430\u0435\u043c\u043e\u0433\u043e \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u0430",
        ]
        for command in extra_commands:
            if command not in message:
                message = (message + "\n" + command).strip()
        if not self.agenda_enabled and self.ADD_AGENDA_COMMAND not in message:
            message = (message + "\n" + self.ADD_AGENDA_COMMAND).strip()
        return message

    def _command_description_text(self) -> str:
        lines = [
            "Описание команд:",
            "",
            "#описание команд",
            "Показывает это сообщение с подробным описанием всех доступных команд.",
            "",
            "#конфиденциально для <имя участника>",
            "Бот завершает запись основной части, удаляет из встречи всех, кроме указанных участников и себя, затем начинает новую запись конфиденциальной части.",
            "Имена пишите в именительном падеже. Если участников несколько, указывайте их через пробел без знаков препинания.",
            "",
            "#конфиденциально без записи для <имя участника>",
            "Бот удаляет из встречи всех, кроме указанных участников, завершает свою запись и выходит из звонка. Конфиденциальная часть не записывается.",
            "",
            "#выход бота",
            "Бот корректно завершает участие во встрече: останавливает запись, сохраняет файлы, запускает транскрипцию и выгрузку материалов.",
            "",
            "#\u0443\u0434\u0430\u043b\u0438\u0442\u044c \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u043e\u0432 <\u0438\u043c\u044f \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u0430>",
            "\u0423\u0434\u0430\u043b\u044f\u0435\u0442 \u0438\u0437 \u0432\u0441\u0442\u0440\u0435\u0447\u0438 \u0443\u043a\u0430\u0437\u0430\u043d\u043d\u044b\u0445 \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u043e\u0432. \u041c\u043e\u0436\u043d\u043e \u043f\u0435\u0440\u0435\u0434\u0430\u0432\u0430\u0442\u044c \u043d\u0435\u0441\u043a\u043e\u043b\u044c\u043a\u043e \u0438\u043c\u0435\u043d \u0447\u0435\u0440\u0435\u0437 \u0437\u0430\u043f\u044f\u0442\u0443\u044e \u0438\u043b\u0438 \u043f\u0440\u043e\u0431\u0435\u043b: #\u0443\u0434\u0430\u043b\u0438\u0442\u044c \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u043e\u0432 \u0418\u0432\u0430\u043d \u0421\u043b\u0430\u0432\u0438\u043d\u0441\u043a\u0438\u0439, \u0410\u043d\u0434\u0440\u0435\u0439 \u0411\u0435\u043b\u044c\u0433\u0438\u043d.",
            "",
            "#\u043f\u0440\u0435\u0434\u043f\u043e\u043b\u0430\u0433\u0430\u0435\u043c\u044b\u0435 \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u0438",
            "\u041f\u043e\u043a\u0430\u0437\u044b\u0432\u0430\u0435\u0442 \u0442\u0435\u043a\u0443\u0449\u0438\u0439 \u0441\u043f\u0438\u0441\u043e\u043a \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u043e\u0432, \u043a\u043e\u0442\u043e\u0440\u044b\u0435 \u0434\u043e\u043b\u0436\u043d\u044b \u0431\u044b\u043b\u0438 \u043f\u0440\u0438\u0439\u0442\u0438 \u043d\u0430 \u0432\u0441\u0442\u0440\u0435\u0447\u0443.",
            "",
            "#\u0434\u043e\u0431\u0430\u0432\u0438\u0442\u044c \u043f\u0440\u0435\u0434\u043f\u043e\u043b\u0430\u0433\u0430\u0435\u043c\u043e\u0433\u043e \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u0430 <\u0438\u043c\u044f \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u0430>",
            "\u0414\u043e\u0431\u0430\u0432\u043b\u044f\u0435\u0442 \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u043e\u0432 \u0432 \u0441\u043f\u0438\u0441\u043e\u043a \u043e\u0436\u0438\u0434\u0430\u0435\u043c\u044b\u0445. \u041c\u043e\u0436\u043d\u043e \u043f\u0438\u0441\u0430\u0442\u044c \u0441 email \u0438\u043b\u0438 \u0431\u0435\u0437: \u0410\u043d\u0434\u0440\u0435\u0439 \u0411\u0435\u043b\u044c\u0433\u0438\u043d - andrey@example.com, \u0418\u0432\u0430\u043d \u0421\u043b\u0430\u0432\u0438\u043d\u0441\u043a\u0438\u0439 <ivan@example.com>.",
            "",
            "#\u0443\u0434\u0430\u043b\u0438\u0442\u044c \u043f\u0440\u0435\u0434\u043f\u043e\u043b\u0430\u0433\u0430\u0435\u043c\u043e\u0433\u043e \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u0430 <\u0438\u043c\u044f \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u0430>",
            "\u0423\u0434\u0430\u043b\u044f\u0435\u0442 \u043e\u0434\u043d\u043e\u0433\u043e \u0438\u043b\u0438 \u043d\u0435\u0441\u043a\u043e\u043b\u044c\u043a\u0438\u0445 \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u043e\u0432 \u0438\u0437 \u0441\u043f\u0438\u0441\u043a\u0430 \u043e\u0436\u0438\u0434\u0430\u0435\u043c\u044b\u0445. \u041f\u043e\u0447\u0442\u0430 \u043f\u0440\u0438 \u0443\u0434\u0430\u043b\u0435\u043d\u0438\u0438 \u043d\u0435 \u043e\u0431\u044f\u0437\u0430\u0442\u0435\u043b\u044c\u043d\u0430.",
            "",
            "#\u0443\u0434\u0430\u043b\u0435\u043d\u0438\u0435 \u043f\u043e\u0447\u0442\u044b \u043f\u0440\u0435\u0434\u043f\u043e\u043b\u0430\u0433\u0430\u0435\u043c\u043e\u0433\u043e \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u0430 <\u0438\u043c\u044f \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u0430>",
            "\u041e\u0447\u0438\u0449\u0430\u0435\u0442 email \u0443 \u0443\u0436\u0435 \u0434\u043e\u0431\u0430\u0432\u043b\u0435\u043d\u043d\u043e\u0433\u043e \u043f\u0440\u0435\u0434\u043f\u043e\u043b\u0430\u0433\u0430\u0435\u043c\u043e\u0433\u043e \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u0430, \u043d\u043e \u0441\u0430\u043c\u043e\u0433\u043e \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u0430 \u0438\u0437 \u0441\u043f\u0438\u0441\u043a\u0430 \u043d\u0435 \u0443\u0434\u0430\u043b\u044f\u0435\u0442.",
            "",
            "#\u0438\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u0435 \u043f\u043e\u0447\u0442\u044b \u043f\u0440\u0435\u0434\u043f\u043e\u043b\u0430\u0433\u0430\u0435\u043c\u043e\u0433\u043e \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u0430 <\u0438\u043c\u044f> - <email>",
            "\u041d\u0430\u0437\u043d\u0430\u0447\u0430\u0435\u0442 \u0438\u043b\u0438 \u0437\u0430\u043c\u0435\u043d\u044f\u0435\u0442 email \u0443 \u0443\u0436\u0435 \u0434\u043e\u0431\u0430\u0432\u043b\u0435\u043d\u043d\u043e\u0433\u043e \u043f\u0440\u0435\u0434\u043f\u043e\u043b\u0430\u0433\u0430\u0435\u043c\u043e\u0433\u043e \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u0430. \u041f\u0440\u0438\u043c\u0435\u0440: #\u0438\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u0435 \u043f\u043e\u0447\u0442\u044b \u043f\u0440\u0435\u0434\u043f\u043e\u043b\u0430\u0433\u0430\u0435\u043c\u043e\u0433\u043e \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u0430 \u0418\u0432\u0430\u043d \u0421\u043b\u0430\u0432\u0438\u043d\u0441\u043a\u0438\u0439 - ivan@example.com.",
        ]
        if not self.agenda_enabled:
            lines.extend([
                "",
                "#добавить повестку",
                "Добавляет повестку во время встречи. Можно передать повестку в этом же сообщении после команды или отправить ее следующим сообщением.",
                "Формат такой же, как в описании встречи. Время и материалы необязательны; материалы отделяются символом |:",
                "###Повестка:",
                "#1. Первый вопрос - 00:10:00 | Материалы: https://example.com",
                "#2. Второй вопрос | Материалы: документ и ссылка",
                "#3. Третий вопрос",
                "###",
            ])
        if self.agenda_enabled:
            lines.extend([
                "",
                "#следующий вопрос",
                "Завершает текущий вопрос и переключает таймер повестки на следующий незавершенный вопрос. Если вопросы закончились, бот сообщит, что повестка завершена.",
                "",
                "#вопрос №",
                "Ставит текущий вопрос на паузу и переключает таймер на указанный незавершенный вопрос. Например: #вопрос №3.",
                "",
                "#конец вопроса",
                "Окончательно завершает текущий вопрос. После этого к нему нельзя вернуться через #вопрос № или #следующий вопрос.",
                "",
                "#вопросы без указания времени",
                "Показывает незавершенные вопросы, для которых не задано плановое время.",
                "",
                "#незавершенные вопросы",
                "Показывает все вопросы повестки, которые еще не были завершены.",
                "",
                "#все вопросы",
                "Показывает всю повестку со статусами, плановым и фактическим временем.",
                "",
                "#назначить время №3 20",
                "Назначает или переназначает плановое время для незавершенного вопроса. Форматы: 20 минут, 1:20 - 1 час 20 минут, 1:20:30 - 1 час 20 минут 30 секунд.",
                "",
                "#добавить вопрос Название вопроса - 20 | Материалы: ссылка",
                "Добавляет новый вопрос в конец повестки. Время и материалы необязательны. Без времени: #добавить вопрос Название вопроса | Материалы: ссылка.",
                "",
                "#пропустить текущий вопрос",
                "Помечает текущий вопрос как пропущенный участником и переводит повестку на следующий незавершенный вопрос.",
                "",
                "#пропустить вопрос №3",
                "Помечает выбранный незавершенный вопрос как пропущенный. Автоматические переходы больше не будут на него попадать.",
            ])
        return "\n".join(lines)

    async def _send_service_message(
        self,
        message: str,
        *,
        deduplicate: bool = True,
        suppress_unverified_retry: bool = True,
    ) -> str:
        normalized = self._normalize_message_text(message)
        if deduplicate and self._is_duplicate_service_message(normalized):
            return "duplicate outgoing service message suppressed"

        before_count = await self._message_text_occurrence_count(message)
        last_result = "not attempted"
        for attempt in range(1, 4):
            result = await self._send_message_to_chat(message)
            last_result = result
            if deduplicate and self._service_message_send_may_have_succeeded(result):
                self._mark_service_message_attempt(normalized)
                self._bot_sent_texts.add(normalized)
            await self.page.wait_for_timeout(1200)
            after_count = await self._message_text_occurrence_count(message)
            if after_count > before_count or await self._latest_visible_message_matches(message):
                return result if attempt == 1 else f"{result}; verified on attempt {attempt}"
            self.logger(
                f"[Bot] Service message was not visible after attempt {attempt}/3: "
                f"{result}; matches {before_count}->{after_count}"
            )
            if suppress_unverified_retry and self._service_message_send_may_have_succeeded(result):
                return f"{result}; duplicate retry suppressed after unverified send"
            await self.page.wait_for_timeout(1200)
        return f"{last_result}; message was not visible after retries"

    def _service_message_dedup_key(self, message: str) -> str:
        return self._normalize_message_text(message).casefold()

    def _is_duplicate_service_message(self, normalized_message: str) -> bool:
        if self._outgoing_message_dedup_seconds <= 0:
            return False
        key = self._service_message_dedup_key(normalized_message)
        if not key:
            return False
        now = time.monotonic()
        last_seen = self._recent_service_message_times.get(key)
        duplicate = last_seen is not None and now - last_seen < self._outgoing_message_dedup_seconds
        for existing_key, seen_at in list(self._recent_service_message_times.items()):
            if now - seen_at > self._outgoing_message_dedup_seconds * 4:
                self._recent_service_message_times.pop(existing_key, None)
        if duplicate:
            self.logger(
                f"[Bot] Duplicate outgoing service message suppressed: "
                f"{key!r}; {now - last_seen:.1f}s since previous send attempt"
            )
        return duplicate

    def _mark_service_message_attempt(self, normalized_message: str) -> None:
        key = self._service_message_dedup_key(normalized_message)
        if key:
            self._recent_service_message_times[key] = time.monotonic()

    @staticmethod
    def _service_message_send_may_have_succeeded(result: str) -> bool:
        lowered = str(result or "").casefold()
        hard_failures = (
            "chat iframe not found",
            "message editor not found",
            "send button not found",
        )
        return not any(failure in lowered for failure in hard_failures)

    async def _send_message_to_chat(self, message: str) -> str:
        chat_frame = await self._wait_for_chat_frame(timeout_ms=10000)
        if chat_frame is None:
            return "chat iframe not found"

        editor = await self._wait_for_message_editor(chat_frame)
        if editor is None:
            await self._write_chat_send_debug(chat_frame, "message editor not found")
            return "message editor not found"

        await editor.click(timeout=5000)
        try:
            await editor.fill(message, timeout=5000)
        except Exception:
            await editor.press("Control+A", timeout=3000)
            await editor.type(message, delay=5, timeout=10000)

        await self.page.wait_for_timeout(300)
        await editor.press("Enter", timeout=5000)
        await self.page.wait_for_timeout(500)
        if await self._editor_text_was_sent(editor, message):
            return "sent via enter"

        send_result = await self._click_send_button_in_frame(chat_frame)
        await self._save_send_attempt_screenshot()
        if send_result != "send button not found":
            return send_result

        return "send action attempted, message may still be in editor"


    async def _save_send_attempt_screenshot(self) -> None:
        try:
            screenshot_path = self._screenshot_path("after_send_attempt")
            await self.page.screenshot(path=str(screenshot_path), full_page=True)
            self.logger(f"[Bot] Chat send attempt screenshot saved: {screenshot_path}")
        except Exception as error:
            self.logger(f"[Bot] Chat send attempt screenshot failed: {error}")

    async def _editor_text_was_sent(self, editor, message: str) -> bool:
        try:
            current_text = await editor.evaluate(
                """(element) => {
                    const value = element.value ?? element.innerText ?? element.textContent ?? '';
                    return String(value).replace(/\\s+/g, ' ').trim();
                }"""
            )
        except Exception:
            return False
        return current_text != self._normalize_message_text(message)

    async def _wait_for_message_editor(self, chat_frame):
        timeout_ms = int(os.getenv("TELEMOST_CHAT_COMMANDS_EDITOR_TIMEOUT_MS", "60000"))
        deadline = datetime.now(timezone.utc).timestamp() + timeout_ms / 1000
        while datetime.now(timezone.utc).timestamp() < deadline:
            editor = await self._find_message_editor(chat_frame)
            if editor is not None:
                return editor
            await self.page.wait_for_timeout(500)
        return None

    async def _find_message_editor(self, chat_frame):
        selectors = [
            'textarea',
            '[contenteditable="true"]',
            '[role="textbox"]',
            'input[type="text"]',
        ]
        for selector in selectors:
            locator = chat_frame.locator(selector)
            count = await locator.count()
            for index in range(count - 1, -1, -1):
                candidate = locator.nth(index)
                try:
                    if await candidate.is_visible(timeout=1000) and await candidate.is_enabled(timeout=1000):
                        return candidate
                except Exception:
                    continue

        return None

    async def _click_send_button_in_frame(self, chat_frame) -> str:
        result = await chat_frame.evaluate("""() => {
            const isVisible = (element) => {
                if (!element) {
                    return false;
                }
                const rect = element.getBoundingClientRect();
                const style = window.getComputedStyle(element);
                return rect.width > 0 && rect.height > 0 &&
                    style.display !== 'none' &&
                    style.visibility !== 'hidden' &&
                    Number(style.opacity || '1') !== 0;
            };
            const clickElement = (element) => {
                if (!element) {
                    return null;
                }
                const clickable = element.closest('button, [role="button"], [data-testid], [class]') || element;
                clickable.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true, pointerId: 1, pointerType: 'mouse', isPrimary: true }));
                clickable.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, button: 0 }));
                clickable.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, cancelable: true, pointerId: 1, pointerType: 'mouse', isPrimary: true }));
                clickable.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, button: 0 }));
                clickable.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 }));
                return clickable;
            };

            const points = [
                [window.innerWidth - 28, window.innerHeight - 28],
                [window.innerWidth - 30, window.innerHeight - 32],
                [window.innerWidth - 36, window.innerHeight - 24],
                [window.innerWidth - 24, window.innerHeight - 36],
            ];
            for (const [x, y] of points) {
                const element = document.elementFromPoint(x, y);
                const clicked = clickElement(element);
                if (clicked) {
                    const rect = clicked.getBoundingClientRect();
                    const label = `${clicked.innerText || ''} ${clicked.textContent || ''} ${clicked.getAttribute?.('aria-label') || ''} ${clicked.getAttribute?.('title') || ''} ${clicked.getAttribute?.('data-testid') || ''} ${clicked.className || ''}`.replace(/\\s+/g, ' ').trim();
                    return `sent via iframe elementFromPoint ${Math.round(x)},${Math.round(y)}: ${label} @ ${Math.round(rect.x)},${Math.round(rect.y)},${Math.round(rect.width)},${Math.round(rect.height)}`;
                }
            }

            const candidates = Array.from(document.querySelectorAll('button, [role="button"], div, span'))
                .filter(isVisible)
                .map((element) => {
                    const rect = element.getBoundingClientRect();
                    const label = `${element.innerText || ''} ${element.textContent || ''} ${element.getAttribute?.('aria-label') || ''} ${element.getAttribute?.('title') || ''} ${element.getAttribute?.('data-testid') || ''} ${element.className || ''}`.toLowerCase();
                    return { element, rect, label };
                })
                .filter((item) => item.rect.y > window.innerHeight * 0.82)
                .sort((left, right) => right.rect.x - left.rect.x);
            const target = candidates[0]?.element;
            const clicked = clickElement(target);
            if (!clicked) {
                return 'send button not found';
            }
            const rect = clicked.getBoundingClientRect();
            return `sent via iframe bottom-right candidate @ ${Math.round(rect.x)},${Math.round(rect.y)},${Math.round(rect.width)},${Math.round(rect.height)}`;
        }""")
        return result

    async def _write_chat_send_debug(self, chat_frame, reason: str) -> None:
        try:
            debug = await chat_frame.evaluate("""(reason) => {
                const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                const visible = (element) => {
                    const rect = element.getBoundingClientRect();
                    const style = window.getComputedStyle(element);
                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                return {
                    strategy: 'chat-send-debug',
                    reason,
                    frameUrl: window.location.href,
                    bodyTextSample: clean(document.body.innerText).slice(0, 1200),
                    editors: Array.from(document.querySelectorAll('textarea, input[type="text"], [contenteditable="true"], [role="textbox"]'))
                        .map((element) => {
                            const rect = element.getBoundingClientRect();
                            return {
                                tag: element.tagName,
                                role: element.getAttribute('role') || '',
                                ariaLabel: element.getAttribute('aria-label') || '',
                                placeholder: element.getAttribute('placeholder') || '',
                                className: typeof element.className === 'string' ? element.className : '',
                                visible: visible(element),
                                x: Math.round(rect.x),
                                y: Math.round(rect.y),
                                width: Math.round(rect.width),
                                height: Math.round(rect.height),
                            };
                        }),
                };
            }""", reason)
            self._write_debug_snapshot(debug)
        except Exception as error:
            self.logger(f"[Bot] Chat send debug failed: {error}")

    async def _wait_for_chat_frame(self, timeout_ms: int = 10000):
        deadline = datetime.now(timezone.utc).timestamp() + timeout_ms / 1000
        while datetime.now(timezone.utc).timestamp() < deadline:
            frame = self._find_chat_frame()
            if frame is not None:
                return frame
            await self.page.wait_for_timeout(250)
        return None

    def _start_message_monitor(self) -> None:
        if self._monitor_task is not None and not self._monitor_task.done():
            return
        self._messages_path().touch(exist_ok=True)
        self._monitor_task = asyncio.create_task(self._monitor_messages_loop())
        self.logger(f"[Bot] Chat messages monitor started: {self._messages_path()}")

    async def _monitor_messages_loop(self) -> None:
        interval_ms = int(os.getenv("TELEMOST_CHAT_COMMANDS_MONITOR_INTERVAL_MS", "5000"))
        try:
            while True:
                try:
                    if self.page.is_closed():
                        return
                    messages = await self._read_visible_chat_messages()
                    new_messages = self._append_new_messages(messages)
                    await self._handle_new_messages(new_messages)
                    self.logger(
                        f"[Bot] Chat monitor scan: {len(messages)} visible candidate(s), {len(new_messages)} new"
                    )
                except Exception as error:
                    self.logger(f"[Bot] Chat messages monitor error: {error}")
                await self.page.wait_for_timeout(interval_ms)
        finally:
            self._cleanup_debug_artifacts()

    async def _read_visible_chat_messages(self) -> list[dict]:
        chat_frame = self._find_chat_frame()
        if chat_frame is None:
            self._write_debug_snapshot(
                {
                    "strategy": "chat-iframe-clean-messages",
                    "reason": "chat iframe not found",
                    "frames": [frame.url for frame in self.page.frames],
                }
            )
            return []

        try:
            result = await chat_frame.evaluate("""() => {
                const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                const isVisible = (element) => {
                    if (!element) {
                        return false;
                    }
                    const rect = element.getBoundingClientRect();
                    const style = window.getComputedStyle(element);
                    return rect.width > 0 && rect.height > 0 &&
                        style.display !== 'none' &&
                        style.visibility !== 'hidden' &&
                        Number(style.opacity || '1') !== 0;
                };

                const readAuthor = (message) => {
                    const row = message.querySelector('.yamb-message-row') || message;
                    const authorElement = row.querySelector('.yamb-message-user__name');
                    if (!authorElement) {
                        return '';
                    }
                    const additional = authorElement.querySelector('.yamb-message-user__additional-text');
                    const additionalText = clean(additional?.innerText || additional?.textContent);
                    let author = clean(authorElement.innerText || authorElement.textContent);
                    if (additionalText && author.endsWith(additionalText)) {
                        author = clean(author.slice(0, -additionalText.length));
                    }
                    return author;
                };

                const readText = (balloon) => {
                    const textParts = Array.from(balloon.querySelectorAll('.yamb-message-text .text, .text'))
                        .filter(isVisible)
                        .map((element) => clean(element.innerText || element.textContent))
                        .filter(Boolean);
                    if (textParts.length > 0) {
                        return textParts.join(' ');
                    }

                    const clone = balloon.cloneNode(true);
                    clone.querySelectorAll('.yamb-message-info, .yamb-message-info__time').forEach((node) => node.remove());
                    return clean(clone.innerText || clone.textContent);
                };

                const readTime = (balloon) => {
                    const timeElement = balloon.querySelector('.yamb-message-info__time, .yamb-message-info');
                    return clean(timeElement?.getAttribute('aria-label') || timeElement?.innerText || timeElement?.textContent);
                };

                const messageNodes = Array.from(document.querySelectorAll('.message'))
                    .filter(isVisible)
                    .sort((left, right) => left.getBoundingClientRect().y - right.getBoundingClientRect().y);
                const messages = [];
                let lastKnownAuthor = '';

                for (const message of messageNodes) {
                    const explicitAuthor = readAuthor(message);
                    if (explicitAuthor) {
                        lastKnownAuthor = explicitAuthor;
                    }
                    const author = explicitAuthor || lastKnownAuthor;
                    const balloons = Array.from(message.querySelectorAll('.yamb-message-balloon, .message-balloon'))
                        .filter(isVisible);

                    for (const balloon of balloons) {
                        const time = readTime(balloon);
                        let text = readText(balloon);
                        if (time && text.endsWith(time)) {
                            text = clean(text.slice(0, -time.length));
                        }
                        if (!text) {
                            continue;
                        }
                        const rect = balloon.getBoundingClientRect();
                        messages.push({
                            author,
                            explicitAuthor,
                            text,
                            time,
                            className: typeof balloon.className === 'string' ? balloon.className : '',
                            x: Math.round(rect.x),
                            y: Math.round(rect.y),
                            width: Math.round(rect.width),
                            height: Math.round(rect.height),
                        });
                    }
                }

                const deduped = messages.filter((item, index, items) => {
                    return items.findIndex((other) =>
                        other.author === item.author &&
                        other.text === item.text &&
                        other.time === item.time &&
                        Math.abs(other.y - item.y) <= 4
                    ) === index;
                }).slice(-100);

                return {
                    messages: deduped,
                    debug: {
                        strategy: 'chat-iframe-clean-messages',
                        frameUrl: window.location.href,
                        bodyTextSample: clean(document.body.innerText).slice(0, 1200),
                        messageNodes: messageNodes.length,
                        cleanMessages: deduped.length,
                        messages: deduped,
                    },
                };
            }""")
        except Exception as error:
            self._write_debug_snapshot(
                {
                    "strategy": "chat-iframe-clean-messages",
                    "reason": "chat frame evaluate failed",
                    "frameUrl": chat_frame.url,
                    "error": str(error),
                    "frames": [frame.url for frame in self.page.frames],
                }
            )
            return []

        self._write_debug_snapshot(result.get("debug", {}))
        return result.get("messages", [])

    async def _handle_new_messages(self, messages: list[dict]) -> None:
        for message in messages:
            key = str(message.get("_message_key") or self._message_key(message))
            if key in self._handled_command_keys:
                continue

            text = str(message.get("text", ""))
            external_source = str(message.get("_external_source") or "")
            source = external_source or "chat"
            if self._is_duplicate_command(text, source):
                self._handled_command_keys.add(key)
                continue

            is_chat_source = not bool(external_source)
            add_agenda_command, agenda_payload = self._parse_add_agenda_command(text)
            if is_chat_source and add_agenda_command:
                self._handled_command_keys.add(key)
                if self.agenda_enabled:
                    result = await self._send_service_message(
                        "Повестка уже добавлена, повторная команда не выполнена."
                    )
                    self.logger(f"[Bot] Duplicate add agenda response result: {result}")
                elif agenda_payload:
                    await self._submit_agenda_from_chat(
                        agenda_payload,
                        message,
                        keep_waiting=False,
                    )
                else:
                    self._start_agenda_submission_wait(message)
                    result = await self._send_service_message(self._agenda_submission_prompt())
                    self.logger(f"[Bot] Agenda submission prompt result: {result}")
                continue

            if (
                is_chat_source
                and not self.agenda_enabled
                and self._is_waiting_for_agenda_from(message)
            ):
                self._handled_command_keys.add(key)
                await self._submit_agenda_from_chat(text, message, keep_waiting=True)
                continue

            if self._is_command_description_command(text):
                self._handled_command_keys.add(key)
                response = self._command_description_text()
                result = await self._send_service_message(response)
                self.logger(f"[Bot] Command description response result: {result}")
                continue

            if self._is_exit_bot_command(text):
                self._handled_command_keys.add(key)
                response = self._exit_bot_response()
                result = await self._send_service_message(response)
                self.logger(f"[Bot] Exit command response result: {result}")
                await self._notify_confidential_event("exit_requested", "", "exit")
                continue

            if self._is_list_expected_participants_command(text):
                self._handled_command_keys.add(key)
                result = await self._send_service_message(self._expected_participants_list_response())
                self.logger(f"[Bot] Expected participants list response result: {result}")
                continue

            add_expected_participants = self._parse_add_expected_participants_command(text)
            if add_expected_participants:
                self._handled_command_keys.add(key)
                update_result = await self._add_expected_participants(add_expected_participants)
                result = await self._send_service_message(self._expected_participants_update_response(update_result, "add"))
                self.logger(f"[Bot] Expected participants add response result: {result}")
                continue

            remove_expected_participants = self._parse_remove_expected_participants_command(text)
            if remove_expected_participants:
                self._handled_command_keys.add(key)
                update_result = await self._remove_expected_participants(remove_expected_participants)
                result = await self._send_service_message(self._expected_participants_update_response(update_result, "remove"))
                self.logger(f"[Bot] Expected participants remove response result: {result}")
                continue

            clear_expected_email = self._parse_clear_expected_participant_email_command(text)
            if clear_expected_email:
                self._handled_command_keys.add(key)
                update_result = await self._clear_expected_participant_email(clear_expected_email)
                result = await self._send_service_message(self._expected_participants_email_response(update_result, "clear_email"))
                self.logger(f"[Bot] Expected participant email clear response result: {result}")
                continue

            change_expected_email = self._parse_change_expected_participant_email_command(text)
            if change_expected_email:
                self._handled_command_keys.add(key)
                update_result = await self._change_expected_participant_email(change_expected_email)
                result = await self._send_service_message(self._expected_participants_email_response(update_result, "change_email"))
                self.logger(f"[Bot] Expected participant email change response result: {result}")
                continue

            delete_participants = self._parse_delete_participants_command(text)
            if delete_participants:
                self._handled_command_keys.add(key)
                response = self._delete_participants_start_response(delete_participants)
                result = await self._send_service_message(response)
                self.logger(f"[Bot] Delete participants start response result: {result}")
                delete_result = await self._delete_participants_by_command(delete_participants)
                try:
                    chat_result = await self._click_chat_button()
                    self.logger(f"[Bot] Chat restored after delete participants command: {chat_result}")
                    await self.page.wait_for_timeout(300)
                except Exception as error:
                    self.logger(f"[Bot] Could not restore chat after delete participants command: {error}")
                final_response = self._delete_participants_result_response(delete_result)
                result = await self._send_service_message(final_response)
                self.logger(f"[Bot] Delete participants final response result: {result}")
                continue

            if not self.agenda_enabled and self._is_any_agenda_command(text):
                self._handled_command_keys.add(key)
                result = await self._send_service_message(self._agenda_not_available_response())
                self.logger(f"[Bot] Agenda command rejected without agenda: {result}")
                continue

            if self.agenda_enabled:
                add_question_text = self._parse_add_agenda_question_command(text)
                if add_question_text is not None:
                    self._handled_command_keys.add(key)
                    agenda_result = await self._notify_agenda_event("add_question", question_text=add_question_text)
                    response = self._agenda_response(agenda_result)
                    if response:
                        result = await self._send_service_message(response)
                        self.logger(f"[Bot] Agenda add question response result: {result}")
                    continue

                skip_question_number = self._parse_skip_agenda_question_command(text)
                if skip_question_number is not None:
                    self._handled_command_keys.add(key)
                    agenda_result = await self._notify_agenda_event("skip_question", question_number=skip_question_number)
                    response = self._agenda_response(agenda_result)
                    if response:
                        result = await self._send_service_message(response)
                        self.logger(f"[Bot] Agenda skip question response result: {result}")
                    continue

                if self._is_skip_current_agenda_command(text):
                    self._handled_command_keys.add(key)
                    agenda_result = await self._notify_agenda_event("skip_current_question")
                    response = self._agenda_response(agenda_result)
                    if response:
                        result = await self._send_service_message(response)
                        self.logger(f"[Bot] Agenda skip current response result: {result}")
                    continue

                assigned_time = self._parse_assign_agenda_time_command(text)
                if assigned_time is not None:
                    question_number, raw_time = assigned_time
                    self._handled_command_keys.add(key)
                    agenda_result = await self._notify_agenda_event("assign_time", question_number=question_number, raw_time=raw_time)
                    response = self._agenda_response(agenda_result)
                    if response:
                        result = await self._send_service_message(response)
                        self.logger(f"[Bot] Agenda assign time response result: {result}")
                    continue

                if self._is_agenda_without_time_command(text):
                    self._handled_command_keys.add(key)
                    agenda_result = await self._notify_agenda_event("questions_without_time")
                    response = self._agenda_response(agenda_result)
                    if response:
                        result = await self._send_service_message(response)
                        self.logger(f"[Bot] Agenda without time response result: {result}")
                    continue

                if self._is_unfinished_agenda_command(text):
                    self._handled_command_keys.add(key)
                    agenda_result = await self._notify_agenda_event("unfinished_questions")
                    response = self._agenda_response(agenda_result)
                    if response:
                        result = await self._send_service_message(response)
                        self.logger(f"[Bot] Agenda unfinished response result: {result}")
                    continue

                if self._is_all_agenda_command(text):
                    self._handled_command_keys.add(key)
                    agenda_result = await self._notify_agenda_event("all_questions")
                    response = self._agenda_response(agenda_result)
                    if response:
                        result = await self._send_service_message(response)
                        self.logger(f"[Bot] Agenda all questions response result: {result}")
                    continue

                question_number = self._parse_switch_agenda_question_command(text)
                if question_number is not None:
                    self._handled_command_keys.add(key)
                    agenda_result = await self._notify_agenda_event("switch_question", question_number=question_number)
                    response = self._agenda_response(agenda_result)
                    if response:
                        result = await self._send_service_message(response)
                        self.logger(f"[Bot] Agenda switch command response result: {result}")
                    continue

                if self._is_end_agenda_question_command(text):
                    self._handled_command_keys.add(key)
                    agenda_result = await self._notify_agenda_event("end_question")
                    response = self._agenda_response(agenda_result)
                    if response:
                        result = await self._send_service_message(response)
                        self.logger(f"[Bot] Agenda end command response result: {result}")
                    continue

                if self._is_next_agenda_command(text):
                    self._handled_command_keys.add(key)
                    agenda_result = await self._notify_agenda_event("next_question")
                    response = self._agenda_response(agenda_result)
                    if response:
                        result = await self._send_service_message(response)
                        self.logger(f"[Bot] Agenda command response result: {result}")
                    continue

            command = self._parse_confidential_command(text)
            if not command:
                continue

            self._handled_command_keys.add(key)

            participants = command["participants"]
            mode = command["mode"]
            if self._confidential_mode is not None:
                response = self._confidential_already_enabled_response(self._confidential_mode)
                result = await self._send_service_message(response)
                self.logger(f"[Bot] Confidential duplicate command response result: {result}")
                continue

            self._confidential_mode = mode
            response = self._confidential_response(participants, mode)
            result = await self._send_service_message(response)
            self.logger(f"[Bot] Confidential command response result: {result}")
            await self._notify_confidential_event("before_participant_cleanup", participants, mode)
            participants_result = await self._open_participants_panel_after_confidential_command(participants)
            self.logger(f"[Bot] Confidential participants panel result: {participants_result}")
            await self._notify_confidential_event("after_participant_cleanup", participants, mode)


    def _parse_command_suffix(self, text: str, prefixes: tuple[str, ...]) -> str | None:
        normalized = self._normalize_message_text(text)
        lowered = normalized.lower()
        for prefix in prefixes:
            if lowered.startswith(prefix):
                suffix = normalized[len(prefix):].strip()
                return suffix or None
        return None

    def _parse_add_expected_participants_command(self, text: str) -> str | None:
        return self._parse_command_suffix(text, self.ADD_EXPECTED_PARTICIPANTS_PREFIXES)

    def _parse_remove_expected_participants_command(self, text: str) -> str | None:
        return self._parse_command_suffix(text, self.REMOVE_EXPECTED_PARTICIPANTS_PREFIXES)

    def _is_list_expected_participants_command(self, text: str) -> bool:
        normalized = self._normalize_message_text(text).lower()
        return normalized in self.LIST_EXPECTED_PARTICIPANTS_COMMANDS

    def _parse_expected_participant_items(self, participants) -> list[dict]:
        if participants is None:
            return []
        if isinstance(participants, list):
            raw_value = participants
        else:
            normalized = self._normalize_message_text(participants)
            has_explicit_separator = any(separator in normalized for separator in (",", ";", "\n", "\r"))
            has_email = "@" in normalized
            words = [word for word in normalized.split() if word]
            if not has_explicit_separator and not has_email and len(words) > 2 and len(words) % 2 == 0:
                raw_value = [" ".join(words[index:index + 2]) for index in range(0, len(words), 2)]
            else:
                raw_value = normalized
        return ParticipantsSummaryBuilder(Path.cwd(), expected_participants=raw_value)._parse_expected_participants(raw_value)

    @staticmethod
    def _expected_participant_label(participant: dict) -> str:
        name = str(participant.get("name") or "").strip()
        email = str(participant.get("email") or "").strip()
        return f"{name} <{email}>" if email else name

    def _expected_participant_key(self, participant: dict) -> str:
        return ParticipantsSummaryBuilder(Path.cwd())._name_key(participant.get("name"))

    async def _notify_expected_participants_updated(self) -> None:
        if self.expected_participants_event_handler is None:
            return
        try:
            result = self.expected_participants_event_handler(list(self._expected_participants))
            if hasattr(result, "__await__"):
                await result
        except Exception as error:
            self.logger(f"[Bot] Expected participants event handler failed: {error}")

    async def _add_expected_participants(self, participants: str) -> dict:
        parsed = self._parse_expected_participant_items(participants)
        by_key = {self._expected_participant_key(item): dict(item) for item in self._expected_participants}
        added = []
        updated = []
        unchanged = []
        for participant in parsed:
            key = self._expected_participant_key(participant)
            if not key:
                continue
            existing = by_key.get(key)
            if existing is None:
                by_key[key] = dict(participant)
                added.append(self._expected_participant_label(participant))
                continue
            if participant.get("email") and participant.get("email") != existing.get("email"):
                existing["email"] = participant.get("email")
                by_key[key] = existing
                updated.append(self._expected_participant_label(existing))
            else:
                unchanged.append(self._expected_participant_label(existing))
        self._expected_participants = sorted(by_key.values(), key=lambda item: str(item.get("name") or "").lower())
        await self._notify_expected_participants_updated()
        return {"added": added, "updated": updated, "unchanged": unchanged, "current": list(self._expected_participants)}

    async def _remove_expected_participants(self, participants: str) -> dict:
        parsed = self._parse_expected_participant_items(participants)
        remove_by_key = {self._expected_participant_key(item): item for item in parsed}
        removed = []
        kept = []
        current_by_key = {self._expected_participant_key(item): item for item in self._expected_participants}
        for key, item in current_by_key.items():
            if key in remove_by_key:
                removed.append(self._expected_participant_label(item))
            else:
                kept.append(item)
        not_found = [
            self._expected_participant_label(item)
            for key, item in remove_by_key.items()
            if key not in current_by_key
        ]
        self._expected_participants = sorted(kept, key=lambda item: str(item.get("name") or "").lower())
        await self._notify_expected_participants_updated()
        return {"removed": removed, "not_found": not_found, "current": list(self._expected_participants)}

    def _expected_participants_list_response(self) -> str:
        if not self._expected_participants:
            return "\u041f\u0440\u0435\u0434\u043f\u043e\u043b\u0430\u0433\u0430\u0435\u043c\u044b\u0435 \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u0438 \u043d\u0435 \u0437\u0430\u0434\u0430\u043d\u044b."
        lines = ["\u041f\u0440\u0435\u0434\u043f\u043e\u043b\u0430\u0433\u0430\u0435\u043c\u044b\u0435 \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u0438:"]
        lines.extend(f"- {self._expected_participant_label(item)}" for item in self._expected_participants)
        return "\n".join(lines)

    def _expected_participants_update_response(self, result: dict, action: str) -> str:
        parts = []
        if action == "add":
            if result.get("added"):
                parts.append("\u0414\u043e\u0431\u0430\u0432\u0438\u043b \u043f\u0440\u0435\u0434\u043f\u043e\u043b\u0430\u0433\u0430\u0435\u043c\u044b\u0445 \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u043e\u0432: " + ", ".join(result["added"]) + ".")
            if result.get("updated"):
                parts.append("\u041e\u0431\u043d\u043e\u0432\u0438\u043b email: " + ", ".join(result["updated"]) + ".")
            if result.get("unchanged"):
                parts.append("\u0423\u0436\u0435 \u0431\u044b\u043b\u0438 \u0432 \u0441\u043f\u0438\u0441\u043a\u0435: " + ", ".join(result["unchanged"]) + ".")
            return " ".join(parts) if parts else "\u041d\u0435 \u0441\u043c\u043e\u0433 \u043e\u0431\u043d\u043e\u0432\u0438\u0442\u044c \u043f\u0440\u0435\u0434\u043f\u043e\u043b\u0430\u0433\u0430\u0435\u043c\u044b\u0445 \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u043e\u0432."
        if result.get("removed"):
            parts.append("\u0423\u0431\u0440\u0430\u043b \u0438\u0437 \u0441\u043f\u0438\u0441\u043a\u0430 \u043f\u0440\u0435\u0434\u043f\u043e\u043b\u0430\u0433\u0430\u0435\u043c\u044b\u0445 \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u043e\u0432: " + ", ".join(result["removed"]) + ".")
        if result.get("not_found"):
            parts.append("\u041d\u0435 \u043d\u0430\u0448\u0435\u043b \u0432 \u0441\u043f\u0438\u0441\u043a\u0435: " + ", ".join(result["not_found"]) + ".")
        return " ".join(parts) if parts else "\u041d\u0435 \u0441\u043c\u043e\u0433 \u043e\u0431\u043d\u043e\u0432\u0438\u0442\u044c \u043f\u0440\u0435\u0434\u043f\u043e\u043b\u0430\u0433\u0430\u0435\u043c\u044b\u0445 \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u043e\u0432 \u0434\u043b\u044f \u0443\u0434\u0430\u043b\u0435\u043d\u0438\u044f."


    def _parse_clear_expected_participant_email_command(self, text: str) -> str | None:
        return self._parse_command_suffix(text, self.CLEAR_EXPECTED_PARTICIPANT_EMAIL_PREFIXES)

    def _parse_change_expected_participant_email_command(self, text: str) -> str | None:
        return self._parse_command_suffix(text, self.CHANGE_EXPECTED_PARTICIPANT_EMAIL_PREFIXES)

    def _expected_participants_by_key(self) -> dict:
        return {self._expected_participant_key(item): dict(item) for item in self._expected_participants}

    async def _clear_expected_participant_email(self, participants: str) -> dict:
        parsed = self._parse_expected_participant_items(participants)
        by_key = self._expected_participants_by_key()
        cleared = []
        already_empty = []
        not_found = []
        for participant in parsed:
            key = self._expected_participant_key(participant)
            item = by_key.get(key)
            label = self._expected_participant_label(participant)
            if not item:
                not_found.append(label)
                continue
            current_label = self._expected_participant_label(item)
            if item.get("email"):
                item["email"] = None
                by_key[key] = item
                cleared.append(current_label)
            else:
                already_empty.append(current_label)
        self._expected_participants = sorted(by_key.values(), key=lambda item: str(item.get("name") or "").lower())
        if cleared:
            await self._notify_expected_participants_updated()
        return {"cleared": cleared, "already_empty": already_empty, "not_found": not_found, "current": list(self._expected_participants)}

    async def _change_expected_participant_email(self, participant_text: str) -> dict:
        parsed = self._parse_expected_participant_items(participant_text)
        with_email = [item for item in parsed if item.get("email")]
        if not with_email:
            return {"invalid": True, "current": list(self._expected_participants)}
        participant = with_email[0]
        key = self._expected_participant_key(participant)
        by_key = self._expected_participants_by_key()
        existing = by_key.get(key)
        if not existing:
            return {"not_found": [self._expected_participant_label(participant)], "current": list(self._expected_participants)}
        old_label = self._expected_participant_label(existing)
        existing["email"] = participant.get("email")
        by_key[key] = existing
        self._expected_participants = sorted(by_key.values(), key=lambda item: str(item.get("name") or "").lower())
        await self._notify_expected_participants_updated()
        return {"changed": [self._expected_participant_label(existing)], "old": [old_label], "current": list(self._expected_participants)}

    def _expected_participants_email_response(self, result: dict, action: str) -> str:
        if result.get("invalid"):
            return "\u041d\u0435 \u043d\u0430\u0448\u0435\u043b email. \u0418\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0439\u0442\u0435 \u0444\u043e\u0440\u043c\u0430\u0442: #\u0438\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u0435 \u043f\u043e\u0447\u0442\u044b \u043f\u0440\u0435\u0434\u043f\u043e\u043b\u0430\u0433\u0430\u0435\u043c\u043e\u0433\u043e \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u0430 \u0418\u0432\u0430\u043d \u0421\u043b\u0430\u0432\u0438\u043d\u0441\u043a\u0438\u0439 - ivan@example.com"
        parts = []
        if action == "clear_email":
            if result.get("cleared"):
                parts.append("\u0423\u0434\u0430\u043b\u0438\u043b \u043f\u043e\u0447\u0442\u0443 \u0443 \u043f\u0440\u0435\u0434\u043f\u043e\u043b\u0430\u0433\u0430\u0435\u043c\u044b\u0445 \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u043e\u0432: " + ", ".join(result["cleared"]) + ".")
            if result.get("already_empty"):
                parts.append("\u041f\u043e\u0447\u0442\u0430 \u0443\u0436\u0435 \u043d\u0435 \u0443\u043a\u0430\u0437\u0430\u043d\u0430: " + ", ".join(result["already_empty"]) + ".")
        if action == "change_email" and result.get("changed"):
            parts.append("\u041e\u0431\u043d\u043e\u0432\u0438\u043b \u043f\u043e\u0447\u0442\u0443 \u043f\u0440\u0435\u0434\u043f\u043e\u043b\u0430\u0433\u0430\u0435\u043c\u043e\u0433\u043e \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u0430: " + ", ".join(result["changed"]) + ".")
        if result.get("not_found"):
            parts.append("\u041d\u0435 \u043d\u0430\u0448\u0435\u043b \u0432 \u0441\u043f\u0438\u0441\u043a\u0435 \u043f\u0440\u0435\u0434\u043f\u043e\u043b\u0430\u0433\u0430\u0435\u043c\u044b\u0445 \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u043e\u0432: " + ", ".join(result["not_found"]) + ".")
        return " ".join(parts) if parts else "\u041d\u0435 \u0441\u043c\u043e\u0433 \u0438\u0437\u043c\u0435\u043d\u0438\u0442\u044c \u043f\u043e\u0447\u0442\u0443 \u043f\u0440\u0435\u0434\u043f\u043e\u043b\u0430\u0433\u0430\u0435\u043c\u043e\u0433\u043e \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u0430."


    def _parse_delete_participants_command(self, text: str) -> str | None:
        normalized = self._normalize_message_text(text)
        lowered = normalized.lower()
        for prefix in self.DELETE_PARTICIPANTS_PREFIXES:
            if lowered.startswith(prefix):
                participants = normalized[len(prefix):].strip()
                return participants or None
        return None

    def _delete_participants_start_response(self, participants: str) -> str:
        names = self._parse_participant_name_items(participants)
        if names:
            return "\u0423\u0434\u0430\u043b\u044f\u044e \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u043e\u0432: " + ", ".join(names) + "."
        return "\u0423\u0434\u0430\u043b\u044f\u044e \u0443\u043a\u0430\u0437\u0430\u043d\u043d\u044b\u0445 \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u043e\u0432."

    def _delete_participants_result_response(self, result: dict) -> str:
        removed = result.get("removed") or []
        not_found = result.get("not_found") or []
        failed = result.get("failed") or []
        parts = []
        if removed:
            parts.append("\u0423\u0434\u0430\u043b\u0435\u043d\u044b: " + ", ".join(removed) + ".")
        if not_found:
            parts.append("\u0423\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u0438 \u043e\u0442\u0441\u0443\u0442\u0441\u0442\u0432\u0443\u044e\u0442 \u0432 \u0437\u0432\u043e\u043d\u043a\u0435: " + ", ".join(not_found) + ".")
        if failed:
            parts.append("\u041d\u0435 \u0441\u043c\u043e\u0433 \u0443\u0434\u0430\u043b\u0438\u0442\u044c: " + ", ".join(failed) + ".")
        return " ".join(parts) if parts else "\u0423\u043a\u0430\u0437\u0430\u043d\u043d\u044b\u0435 \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u0438 \u043e\u0442\u0441\u0443\u0442\u0441\u0442\u0432\u0443\u044e\u0442 \u0432 \u0437\u0432\u043e\u043d\u043a\u0435."


    def _parse_add_agenda_command(self, text: str) -> tuple[bool, str | None]:
        normalized = self._normalize_message_text(text)
        lowered = normalized.lower()
        if lowered == self.ADD_AGENDA_COMMAND:
            return True, None
        prefix = self.ADD_AGENDA_COMMAND + " "
        if lowered.startswith(prefix):
            payload = normalized[len(self.ADD_AGENDA_COMMAND):].strip()
            return True, payload or None
        return False, None

    @staticmethod
    def _agenda_message_author(message: dict) -> str:
        return " ".join(str(message.get("author") or "").strip().lower().split())

    def _start_agenda_submission_wait(self, message: dict) -> None:
        self._agenda_submission_author = self._agenda_message_author(message)
        self._agenda_submission_deadline = (
            datetime.now(timezone.utc).timestamp() + self._agenda_submission_timeout_seconds
        )
        self.logger(
            f"[Bot] Waiting for agenda from chat author: "
            f"{message.get('author') or '<unknown>'}"
        )

    def _clear_agenda_submission_wait(self) -> None:
        self._agenda_submission_author = None
        self._agenda_submission_deadline = None

    def _is_waiting_for_agenda_from(self, message: dict) -> bool:
        if self._agenda_submission_deadline is None:
            return False
        if datetime.now(timezone.utc).timestamp() > self._agenda_submission_deadline:
            self.logger("[Bot] Chat agenda submission wait expired")
            self._clear_agenda_submission_wait()
            return False
        return self._agenda_message_author(message) == (self._agenda_submission_author or "")

    @staticmethod
    def _agenda_submission_prompt() -> str:
        return (
            "Отправьте повестку следующим сообщением в формате:\n"
            "###Повестка:\n"
            "#1. Первый вопрос - 00:10:00 | Материалы: https://example.com\n"
            "#2. Второй вопрос | Материалы: документ и ссылка\n"
            "#3. Третий вопрос\n"
            "###\n"
            "Время и материалы необязательны; материалы отделяются символом |."
        )

    @staticmethod
    def _invalid_agenda_response(error: str | None) -> str:
        reason = error or "не удалось распознать пункты"
        return (
            f"Не удалось добавить повестку: {reason}.\n"
            "Используйте формат:\n"
            "###Повестка:\n"
            "#1. Первый вопрос - 00:10:00 | Материалы: https://example.com\n"
            "#2. Второй вопрос | Материалы: документ и ссылка\n"
            "#3. Третий вопрос\n"
            "###\n"
            "Время и материалы необязательны; материалы отделяются символом |."
        )

    async def _submit_agenda_from_chat(
        self,
        raw_agenda: str,
        message: dict,
        keep_waiting: bool,
    ) -> None:
        agenda_result = await self._notify_agenda_event(
            "activate_agenda",
            raw_agenda=raw_agenda,
            source="chat",
            author=str(message.get("author") or ""),
        )
        status = (agenda_result or {}).get("status")
        if status == "agenda_activated":
            self.agenda_enabled = True
            self._clear_agenda_submission_wait()
            response = self._agenda_activated_commands_text(agenda_result or {})
            result = await self._send_service_message(response)
            self.logger(f"[Bot] Agenda activated chat response result: {result}")
            await self._notify_agenda_event("agenda_commands_announced", source="chat")
            return
        if status == "already_active":
            self.agenda_enabled = True
            self._clear_agenda_submission_wait()
            result = await self._send_service_message(
                "Повестка уже добавлена, повторная команда не выполнена."
            )
            self.logger(f"[Bot] Duplicate agenda activation response result: {result}")
            return

        if keep_waiting:
            self._start_agenda_submission_wait(message)
        else:
            self._clear_agenda_submission_wait()
        response = self._invalid_agenda_response((agenda_result or {}).get("error"))
        result = await self._send_service_message(response)
        self.logger(f"[Bot] Invalid agenda response result: {result}")

    def _agenda_activated_commands_text(self, agenda_result: dict) -> str:
        count = int(agenda_result.get("items_count") or 0)
        command_message = self._startup_message_text()
        lines = command_message.splitlines()
        status_line = f"Повестка добавлена: {count} пункт(ов)."
        if lines and lines[0].strip() == "Бот подключен.":
            lines[0] = status_line
            return "\n".join(lines)
        return status_line + "\n" + command_message

    def _is_command_description_command(self, text: str) -> bool:
        normalized = self._normalize_message_text(text).lower()
        return normalized in self.COMMAND_DESCRIPTION_COMMANDS

    def _is_next_agenda_command(self, text: str) -> bool:
        normalized = self._normalize_message_text(text).lower()
        return normalized in self.NEXT_AGENDA_COMMANDS

    def _is_any_agenda_command(self, text: str) -> bool:
        return (
            self._parse_add_agenda_question_command(text) is not None
            or self._parse_skip_agenda_question_command(text) is not None
            or self._is_skip_current_agenda_command(text)
            or self._parse_assign_agenda_time_command(text) is not None
            or self._is_agenda_without_time_command(text)
            or self._is_unfinished_agenda_command(text)
            or self._is_all_agenda_command(text)
            or self._parse_switch_agenda_question_command(text) is not None
            or self._is_end_agenda_question_command(text)
            or self._is_next_agenda_command(text)
        )

    def _agenda_not_available_response(self) -> str:
        return "\u041f\u043e\u0432\u0435\u0441\u0442\u043a\u0430 \u043d\u0435 \u043f\u0435\u0440\u0435\u0434\u0430\u043d\u0430 \u0434\u043b\u044f \u044d\u0442\u043e\u0439 \u0432\u0441\u0442\u0440\u0435\u0447\u0438, \u043a\u043e\u043c\u0430\u043d\u0434\u044b \u043f\u043e\u0432\u0435\u0441\u0442\u043a\u0438 \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u043d\u044b."

    def _is_end_agenda_question_command(self, text: str) -> bool:
        normalized = self._normalize_message_text(text).lower()
        return normalized in self.END_AGENDA_QUESTION_COMMANDS

    def _parse_switch_agenda_question_command(self, text: str) -> int | None:
        normalized = self._normalize_message_text(text).lower()
        match = self.SWITCH_AGENDA_QUESTION_RE.match(normalized)
        if not match:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    def _is_agenda_without_time_command(self, text: str) -> bool:
        normalized = self._normalize_message_text(text).lower()
        return normalized in self.AGENDA_WITHOUT_TIME_COMMANDS

    def _is_unfinished_agenda_command(self, text: str) -> bool:
        normalized = self._normalize_message_text(text).lower()
        return normalized in self.UNFINISHED_AGENDA_COMMANDS

    def _is_all_agenda_command(self, text: str) -> bool:
        normalized = self._normalize_message_text(text).lower()
        return normalized in self.ALL_AGENDA_COMMANDS

    def _parse_assign_agenda_time_command(self, text: str) -> tuple[int, str] | None:
        normalized = self._normalize_message_text(text).lower()
        match = self.ASSIGN_AGENDA_TIME_RE.match(normalized)
        if not match:
            return None
        try:
            return int(match.group(1)), match.group(2)
        except ValueError:
            return None

    def _parse_add_agenda_question_command(self, text: str) -> str | None:
        normalized = self._normalize_message_text(text)
        match = self.ADD_AGENDA_QUESTION_RE.match(normalized)
        if not match:
            return None
        question = " ".join(match.group(1).strip().split())
        return question or None

    def _is_skip_current_agenda_command(self, text: str) -> bool:
        normalized = self._normalize_message_text(text).lower()
        return normalized in self.SKIP_CURRENT_AGENDA_COMMANDS

    def _parse_skip_agenda_question_command(self, text: str) -> int | None:
        normalized = self._normalize_message_text(text).lower()
        match = self.SKIP_AGENDA_QUESTION_RE.match(normalized)
        if not match:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    async def _notify_agenda_event(self, stage: str, **payload) -> dict | None:
        if self.agenda_event_handler is None:
            return None
        try:
            result = self.agenda_event_handler(stage, **payload)
            if hasattr(result, "__await__"):
                result = await result
            return result
        except Exception as error:
            self.logger(f"[Bot] Agenda event handler failed ({stage}): {error}")
            return {"status": "failed", "message": str(error)}

    @staticmethod
    def _agenda_response_with_materials(message: str, result: dict) -> str:
        materials = " ".join(str(result.get("materials") or "").strip().split())
        if not materials:
            return message
        return f"{message}\nМатериалы: {materials}"

    def _agenda_response(self, result: dict | None) -> str:
        if not result:
            return ""
        status = result.get("status")
        if status == "switched":
            response = (
                "Перехожу к вопросу "
                f"{result.get('index')}/{result.get('total')}: {result.get('title')}"
            )
            return self._agenda_response_with_materials(response, result)
        if status == "items":
            return self._agenda_items_response(result)
        if status == "time_assigned":
            return (
                f"Назначил время для вопроса №{result.get('number')}: "
                f"{result.get('planned_time')} — {result.get('title')}"
            )
        if status == "question_added":
            plan = result.get("planned_time") or "без времени"
            response = f"Добавил вопрос №{result.get('number')}: {result.get('title')} — план: {plan}"
            return self._agenda_response_with_materials(response, result)
        if status == "invalid_question":
            return "Не смог добавить вопрос: текст вопроса пустой."
        if status == "question_skipped":
            skipped = result.get("skipped") or {}
            return f"Вопрос №{skipped.get('number')} пропущен: {skipped.get('title')}"
        if status == "already_skipped":
            return f"Вопрос №{result.get('number')} уже пропущен."
        if status == "skipped":
            return f"Вопрос №{result.get('number')} пропущен и не может быть открыт."
        if status == "skipped_and_switched":
            skipped = result.get("skipped") or {}
            return (
                f"Вопрос №{skipped.get('number')} пропущен. "
                f"Перехожу к вопросу {result.get('index')}/{result.get('total')}: {result.get('title')}"
            )
        if status == "skipped_completed":
            skipped = result.get("skipped") or {}
            return f"Вопрос №{skipped.get('number')} пропущен. Повестка завершена."
        if status == "skipped_no_next":
            skipped = result.get("skipped") or {}
            return f"Вопрос №{skipped.get('number')} пропущен. Следующих незавершенных вопросов нет."
        if status == "invalid_time":
            return "Не смог распознать время. Используйте формат: #назначить время №3 10:30"
        if status == "already_active":
            response = f"Вопрос уже активен: {result.get('index')}/{result.get('total')}: {result.get('title')}"
            return self._agenda_response_with_materials(response, result)
        if status == "not_found":
            return f"В повестке нет вопроса №{result.get('number')}."
        if status == "locked":
            return f"Вопрос №{result.get('number')} уже завершен, изменить его нельзя."
        if status == "closed":
            return "Текущий вопрос завершен. Следующих незавершенных вопросов нет."
        if status == "no_next":
            return "Следующих незавершенных вопросов нет."
        if status == "inactive":
            return "Сейчас нет активного вопроса повестки."
        if status == "completed":
            return "Повестка завершена."
        return ""

    @staticmethod
    def _confidential_already_enabled_response(mode: str) -> str:
        if mode == "no_recording":
            return "\u041a\u043e\u043d\u0444\u0438\u0434\u0435\u043d\u0446\u0438\u0430\u043b\u044c\u043d\u044b\u0439 \u0440\u0435\u0436\u0438\u043c \u0431\u0435\u0437 \u0437\u0430\u043f\u0438\u0441\u0438 \u0443\u0436\u0435 \u0432\u043a\u043b\u044e\u0447\u0435\u043d."
        return "\u041a\u043e\u043d\u0444\u0438\u0434\u0435\u043d\u0446\u0438\u0430\u043b\u044c\u043d\u044b\u0439 \u0440\u0435\u0436\u0438\u043c \u0443\u0436\u0435 \u0432\u043a\u043b\u044e\u0447\u0435\u043d."


    def _agenda_items_response(self, result: dict) -> str:
        items = result.get("items") or []
        kind = result.get("kind")
        titles = {
            "without_time": "Незавершенные вопросы без времени:",
            "unfinished": "Незавершенные вопросы:",
            "all": "Все вопросы повестки:",
        }
        empty = {
            "without_time": "Незавершенных вопросов без времени нет.",
            "unfinished": "Незавершенных вопросов нет.",
            "all": "В повестке нет вопросов.",
        }
        if not items:
            return empty.get(kind, "Вопросов нет.")
        lines = [titles.get(kind, "Вопросы повестки:")]
        for item in items:
            plan = item.get("planned_time") or "без времени"
            actual = item.get("actual_time") or "0:00"
            status = self._agenda_status_label(str(item.get("status") or ""), bool(item.get("locked")))
            lines.append(f"№{item.get('number')}. {item.get('title')} — план: {plan}, факт: {actual}, статус: {status}")
            materials = " ".join(str(item.get("materials") or "").strip().split())
            if materials:
                lines.append(f"  Материалы: {materials}")
        return "\n".join(lines)

    @staticmethod
    def _agenda_status_label(status: str, locked: bool) -> str:
        if status == "in_progress":
            return "идет"
        if status == "paused":
            return "на паузе"
        if status == "within_time":
            return "уложились"
        if status == "over_time":
            return "превышено"
        if status == "completed_without_plan":
            return "завершен без лимита"
        if status == "skipped_by_participant":
            return "пропущен"
        if locked:
            return "завершен"
        return "не начат"

    async def _notify_confidential_event(self, stage: str, participants: str, mode: str = "recording") -> None:
        if self.confidential_event_handler is None:
            return
        try:
            result = self.confidential_event_handler(stage, participants, mode)
            if hasattr(result, "__await__"):
                await result
        except Exception as error:
            self.logger(f"[Bot] Confidential event handler failed ({stage}): {error}")

    async def _open_participants_panel_after_confidential_command(self, participants: str) -> str:
        protected_names = self._parse_protected_participant_names(participants)
        click_result = await self._click_participants_button()
        await self.page.wait_for_timeout(500)
        screenshot_path = self._screenshot_path("confidential_participants")
        await self.page.screenshot(path=str(screenshot_path), full_page=True)
        self.logger(f"[Bot] Confidential participants screenshot saved: {screenshot_path}")

        action_result = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "bot_id": self.bot_id,
            "protected_names": sorted(protected_names),
            "click_result": click_result,
            "removed": [],
            "stopped_at": None,
        }

        max_removals = int(os.getenv("TELEMOST_CHAT_COMMANDS_MAX_REMOVALS", "20"))
        for attempt in range(1, max_removals + 1):
            menu_result = await self._click_participant_actions_button(protected_names)
            await self.page.wait_for_timeout(500)
            if menu_result.startswith("no removable participants") or menu_result.startswith("participant actions button not found"):
                action_result["stopped_at"] = menu_result
                break

            menu_screenshot_path = self._screenshot_path(f"confidential_participant_menu_{attempt}")
            await self.page.screenshot(path=str(menu_screenshot_path), full_page=True)
            self.logger(f"[Bot] Confidential participant menu screenshot saved: {menu_screenshot_path}")

            remove_result = await self._click_remove_from_meeting_option()
            await self.page.wait_for_timeout(700)
            remove_screenshot_path = self._screenshot_path(f"confidential_remove_from_meeting_{attempt}")
            await self.page.screenshot(path=str(remove_screenshot_path), full_page=True)
            self.logger(f"[Bot] Confidential remove participant screenshot saved: {remove_screenshot_path}")

            confirm_result = await self._click_confirm_remove_from_meeting_button()
            await self.page.wait_for_timeout(1200)
            confirmed_screenshot_path = self._screenshot_path(f"confidential_remove_confirmed_{attempt}")
            await self.page.screenshot(path=str(confirmed_screenshot_path), full_page=True)
            self.logger(f"[Bot] Confidential remove confirmed screenshot saved: {confirmed_screenshot_path}")

            action_result["removed"].append(
                {
                    "attempt": attempt,
                    "menu_result": menu_result,
                    "remove_result": remove_result,
                    "confirm_result": confirm_result,
                }
            )

            if not remove_result.startswith("clicked remove") or not confirm_result.startswith("clicked confirm remove"):
                action_result["stopped_at"] = "removal failed"
                break

            await self.page.wait_for_timeout(800)
        else:
            action_result["stopped_at"] = f"max removals reached: {max_removals}"

        action_debug_path = Path(f"chat_commands_action_debug_{self.bot_id}.jsonl")
        with action_debug_path.open("a", encoding="utf-8") as debug_file:
            debug_file.write(json.dumps(action_result, ensure_ascii=False) + "\n")
        return json.dumps(action_result, ensure_ascii=False)


    async def _visible_participant_names(self) -> list[str]:
        rows = self.page.locator('div[class*="Participant_"]')
        row_count = await rows.count()
        bot_name_markers = (
            "\u0412\u0435\u0440\u0442\u0435\u0440 \u0420\u043e\u0431\u043e\u0442",
            "Telemost Bot",
        )
        names = []
        seen = set()
        for index in range(row_count):
            row = rows.nth(index)
            try:
                if not await row.is_visible(timeout=300):
                    continue
                row_text = " ".join((await row.inner_text(timeout=500)).split())
                if any(marker.lower() in row_text.lower() for marker in bot_name_markers):
                    continue
                display_name = await self._participant_display_name(row)
                participant_name = display_name or row_text
                normalized = self._normalize_participant_name(participant_name)
                if normalized and normalized not in seen:
                    seen.add(normalized)
                    names.append(participant_name)
            except Exception:
                continue
        return names


    async def _delete_participants_by_command(self, participants: str) -> dict:
        requested_display_names = self._parse_participant_name_items(participants)
        click_result = await self._click_participants_button()
        await self.page.wait_for_timeout(500)
        visible_participants = await self._visible_participant_names()
        visible_by_normalized = {
            self._normalize_participant_name(name): name
            for name in visible_participants
        }
        target_display_names = []
        not_found_display_names = []
        for name in requested_display_names:
            normalized = self._normalize_participant_name(name)
            if normalized in visible_by_normalized:
                target_display_names.append(visible_by_normalized[normalized])
            else:
                not_found_display_names.append(name)
        target_names = {self._normalize_participant_name(name) for name in target_display_names if self._normalize_participant_name(name)}

        action_result = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "bot_id": self.bot_id,
            "target_names": sorted(target_names),
            "target_display_names": target_display_names,
            "requested_display_names": requested_display_names,
            "visible_participants": visible_participants,
            "click_result": click_result,
            "removed": [],
            "failed": [],
            "not_found": list(not_found_display_names),
            "attempts": [],
            "stopped_at": None,
        }

        if not target_names:
            action_result["stopped_at"] = "no requested participants visible"
            action_debug_path = Path(f"chat_commands_action_debug_{self.bot_id}.jsonl")
            with action_debug_path.open("a", encoding="utf-8") as debug_file:
                debug_file.write(json.dumps(action_result, ensure_ascii=False) + "\n")
            return action_result

        pending = set(target_names)
        max_removals = int(os.getenv("TELEMOST_CHAT_COMMANDS_MAX_REMOVALS", "20"))
        for attempt in range(1, max_removals + 1):
            if not pending:
                action_result["stopped_at"] = "all targets removed"
                break

            menu_result = await self._click_participant_actions_button(target_names=pending)
            await self.page.wait_for_timeout(500)
            if (
                menu_result.startswith("no target participants found")
                or menu_result.startswith("participant actions button not found")
                or menu_result.startswith("participant actions menu not opened")
            ):
                action_result["stopped_at"] = menu_result
                break

            remove_result = await self._click_remove_from_meeting_option()
            await self.page.wait_for_timeout(700)
            confirm_result = await self._click_confirm_remove_from_meeting_button()
            await self.page.wait_for_timeout(1200)

            removed_name = self._participant_name_from_menu_result(menu_result)
            normalized_removed_name = self._normalize_participant_name(removed_name)
            attempt_result = {
                "attempt": attempt,
                "participant": removed_name,
                "menu_result": menu_result,
                "remove_result": remove_result,
                "confirm_result": confirm_result,
            }
            action_result["attempts"].append(attempt_result)

            if remove_result.startswith("clicked remove") and confirm_result.startswith("clicked confirm remove"):
                if normalized_removed_name:
                    pending.discard(normalized_removed_name)
                    action_result["removed"].append(removed_name)
                else:
                    action_result["removed"].append("unknown participant")
            else:
                failed_label = removed_name or menu_result
                action_result["failed"].append(failed_label)
                action_result["stopped_at"] = "removal failed"
                break

            await self.page.wait_for_timeout(800)
        else:
            action_result["stopped_at"] = f"max removals reached: {max_removals}"

        if pending:
            display_by_normalized = {
                self._normalize_participant_name(name): name
                for name in target_display_names
            }
            action_result["not_found"].extend(
                display_by_normalized.get(name, name)
                for name in sorted(pending)
            )

        action_debug_path = Path(f"chat_commands_action_debug_{self.bot_id}.jsonl")
        with action_debug_path.open("a", encoding="utf-8") as debug_file:
            debug_file.write(json.dumps(action_result, ensure_ascii=False) + "\n")
        return action_result


    async def _click_confirm_remove_from_meeting_button(self) -> str:
        result = await self.page.evaluate("""() => {
            const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
            const removeText = '\\u0423\\u0434\\u0430\\u043b\\u0438\\u0442\\u044c';
            const isVisible = (element) => {
                if (!element) {
                    return false;
                }
                const rect = element.getBoundingClientRect();
                const style = window.getComputedStyle(element);
                return rect.width > 0 && rect.height > 0 &&
                    style.display !== 'none' &&
                    style.visibility !== 'hidden' &&
                    Number(style.opacity || '1') !== 0 &&
                    !element.disabled;
            };
            const isDisabled = (element) => {
                const disabledAttr = element.getAttribute('aria-disabled') === 'true' || element.getAttribute('data-disabled') === 'true';
                const className = typeof element.className === 'string' ? element.className.toLowerCase() : '';
                return disabledAttr || className.includes('disabled');
            };
            const clickElement = (element) => {
                element.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true, pointerId: 1, pointerType: 'mouse', isPrimary: true }));
                element.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, button: 0 }));
                element.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, cancelable: true, pointerId: 1, pointerType: 'mouse', isPrimary: true }));
                element.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, button: 0 }));
                element.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 }));
            };
            const describe = (element) => {
                const rect = element.getBoundingClientRect();
                const text = clean(element.innerText || element.textContent || element.getAttribute('title') || '');
                return `${text || 'unknown'} @ ${Math.round(rect.x)},${Math.round(rect.y)},${Math.round(rect.width)},${Math.round(rect.height)}`;
            };

            const actionBars = Array.from(document.querySelectorAll('[data-testid="orb-modal-action-bar"], .Orb-Modal2-ActionBar'))
                .filter(isVisible);
            const actionBar = actionBars[actionBars.length - 1] || document.body;
            const buttons = Array.from(actionBar.querySelectorAll('button, [role="button"]'))
                .filter(isVisible)
                .filter((element) => !isDisabled(element))
                .map((element) => ({
                    element,
                    text: clean(element.innerText || element.textContent),
                    className: typeof element.className === 'string' ? element.className : '',
                }))
                .filter((item) => item.text === removeText || item.text.includes(removeText))
                .sort((left, right) => {
                    const leftBrand = left.className.includes('Orb-Button_view_brand') ? 0 : 1;
                    const rightBrand = right.className.includes('Orb-Button_view_brand') ? 0 : 1;
                    return leftBrand - rightBrand;
                });

            const target = buttons[0]?.element;
            if (!target) {
                return 'confirm remove button not found';
            }
            clickElement(target);
            return `clicked confirm remove: ${describe(target)}`;
        }""")
        return result

    async def _click_remove_from_meeting_option(self) -> str:
        result = await self.page.evaluate(r"""async () => {
            const clean = (value) => (value || '').replace(/\s+/g, ' ').trim();
            const removeText = '\u0423\u0434\u0430\u043b\u0438\u0442\u044c \u0441\u043e \u0432\u0441\u0442\u0440\u0435\u0447\u0438';
            const confirmTitle = '\u0423\u0434\u0430\u043b\u0438\u0442\u044c \u0441\u043e \u0432\u0441\u0442\u0440\u0435\u0447\u0438?';
            const afterRemoveText = '\u041f\u043e\u0441\u043b\u0435 \u0443\u0434\u0430\u043b\u0435\u043d\u0438\u044f';
            const isVisible = (element) => {
                if (!element) return false;
                const rect = element.getBoundingClientRect();
                const style = window.getComputedStyle(element);
                return rect.width > 0 && rect.height > 0 &&
                    style.display !== 'none' &&
                    style.visibility !== 'hidden' &&
                    Number(style.opacity || '1') !== 0;
            };
            const modalIsOpen = () => {
                const text = document.body?.innerText || '';
                return text.includes(confirmTitle) || text.includes(afterRemoveText);
            };
            const clickElement = async (element) => {
                element.scrollIntoView({ block: 'center', inline: 'center' });
                await new Promise((resolve) => setTimeout(resolve, 150));
                const rect = element.getBoundingClientRect();
                const x = rect.left + rect.width / 2;
                const y = rect.top + rect.height / 2;
                const target = document.elementFromPoint(x, y) || element;
                const clickable = target.closest?.('div[class*="option_"], button, [role="button"]') || element;
                clickable.focus?.({ preventScroll: true });
                const pointer = (type, buttons = 0) => new PointerEvent(type, {
                    bubbles: true, cancelable: true, composed: true, view: window,
                    button: 0, buttons, clientX: x, clientY: y, screenX: x, screenY: y,
                    pointerId: 1, pointerType: 'mouse', isPrimary: true,
                });
                const mouse = (type, buttons = 0) => new MouseEvent(type, {
                    bubbles: true, cancelable: true, composed: true, view: window,
                    button: 0, buttons, clientX: x, clientY: y, screenX: x, screenY: y,
                });
                clickable.dispatchEvent(pointer('pointerover'));
                clickable.dispatchEvent(mouse('mouseover'));
                clickable.dispatchEvent(pointer('pointerdown', 1));
                clickable.dispatchEvent(mouse('mousedown', 1));
                await new Promise((resolve) => setTimeout(resolve, 80));
                clickable.dispatchEvent(pointer('pointerup'));
                clickable.dispatchEvent(mouse('mouseup'));
                clickable.dispatchEvent(mouse('click'));
                clickable.click?.();
                await new Promise((resolve) => setTimeout(resolve, 500));
                return `${clean(clickable.innerText || clickable.textContent || clickable.getAttribute?.('title') || '')} @ ${Math.round(x)},${Math.round(y)}`;
            };

            const popovers = Array.from(document.querySelectorAll('[data-testid="orb-popover"], [data-test-id="more-button-popover"], [class*="ModerationPopup"]'))
                .filter(isVisible)
                .filter((element) => (element.innerText || element.textContent || '').includes(removeText));
            const popover = popovers[popovers.length - 1];
            if (!popover) {
                return 'remove popover not found near participant actions button';
            }

            const candidates = Array.from(popover.querySelectorAll('div[class*="option_"], [title]'))
                .filter(isVisible)
                .map((element) => ({
                    element,
                    text: clean(element.innerText || element.textContent || ''),
                    title: clean(element.getAttribute('title') || ''),
                    rect: element.getBoundingClientRect(),
                }))
                .filter((item) => item.title === removeText || item.text.includes(removeText))
                .sort((left, right) => left.rect.y - right.rect.y);

            const option = candidates[0]?.element;
            if (!option) {
                return `remove option not found inside selected popover; popovers=${popovers.length}`;
            }

            const clicked = await clickElement(option);
            if (modalIsOpen()) {
                return `clicked remove from meeting: ${clicked}`;
            }
            return `remove option clicked but modal not opened: ${clicked}; popovers=${popovers.length}`;
        }""")
        return result

    async def _click_participant_actions_button(self, protected_names: set[str] | None = None, target_names: set[str] | None = None) -> str:
        """Open moderation menu for the next participant matching moderation filters."""
        protected_names = protected_names or set()
        target_names = target_names or set()
        rows = self.page.locator('div[class*="Participant_"]')
        row_count = await rows.count()
        bot_name_markers = (
            "\u0412\u0435\u0440\u0442\u0435\u0440 \u0420\u043e\u0431\u043e\u0442",
            "Telemost Bot",
        )
        remove_text = "\u0423\u0434\u0430\u043b\u0438\u0442\u044c \u0441\u043e \u0432\u0441\u0442\u0440\u0435\u0447\u0438"

        candidates = []
        skipped = []
        for index in range(row_count):
            row = rows.nth(index)
            try:
                if not await row.is_visible(timeout=500):
                    continue
                box = await row.bounding_box(timeout=500)
                if not box or box["width"] <= 0 or box["height"] < 32:
                    continue
                display_name = await self._participant_display_name(row)
                row_text = " ".join((await row.inner_text(timeout=1000)).split())
                participant_name = display_name or row_text
                normalized_name = self._normalize_participant_name(participant_name)
                if any(marker.lower() in row_text.lower() for marker in bot_name_markers):
                    skipped.append(f"bot:{participant_name}")
                    continue
                if normalized_name in protected_names:
                    skipped.append(f"protected:{participant_name}")
                    continue
                if target_names and normalized_name not in target_names:
                    skipped.append(f"not-target:{participant_name}")
                    continue

                # Telemost renders the moderation "more" button lazily: it may appear
                # only after the participant row is hovered. Check it after hover.
                y = box["y"] + box["height"] / 2
                await self.page.mouse.move(box["x"] + 18, y)
                await self.page.wait_for_timeout(120)
                await self.page.mouse.move(box["x"] + box["width"] - 20, y, steps=12)
                await self.page.wait_for_timeout(350)

                more_count = await row.locator('button[data-testid="more-popup-alt-button"]').count()
                if more_count <= 0:
                    more_count = await row.locator('button[aria-label="\u0415\u0449\u0451"], button[title="\u0415\u0449\u0451"]').count()
                if more_count <= 0:
                    skipped.append(f"no-more-after-hover:{participant_name}")
                    continue

                box = await row.bounding_box(timeout=500) or box
                candidates.append((box["y"], row, participant_name, box))
            except Exception as error:
                skipped.append(f"row-error:{error}")
                continue

        if not candidates:
            details = "; ".join(skipped[-8:])
            if target_names:
                return f"no target participants found; targets={sorted(target_names)}; skipped={details}"
            return f"no removable participants found; protected={sorted(protected_names)}; skipped={details}"

        async def popover_is_open() -> bool:
            return await self.page.evaluate(
                """(removeText) => Array.from(document.querySelectorAll('[data-testid="orb-popover"], [data-test-id="more-button-popover"], [class*="ModerationPopup"]'))
                    .some((element) => {
                        const rect = element.getBoundingClientRect();
                        const style = window.getComputedStyle(element);
                        const text = element.innerText || element.textContent || '';
                        return rect.width > 0 && rect.height > 0 &&
                            style.display !== 'none' &&
                            style.visibility !== 'hidden' &&
                            text.includes(removeText);
                    })""",
                remove_text,
            )

        errors = []
        for _, row, participant_name, row_box in sorted(candidates, key=lambda item: item[0]):
            try:
                y = row_box["y"] + row_box["height"] / 2
                await self.page.mouse.move(row_box["x"] + 18, y)
                await self.page.wait_for_timeout(150)
                await self.page.mouse.move(row_box["x"] + row_box["width"] - 20, y, steps=20)
                await self.page.wait_for_timeout(450)

                dom_result = await row.evaluate(
                    """async (row) => {
                        const button = row.querySelector('button[data-testid="more-popup-alt-button"]');
                        if (!button) return 'dom button not found';
                        button.focus?.({ preventScroll: true });
                        button.click?.();
                        return 'dom button click after real hover';
                    }"""
                )
                await self.page.wait_for_timeout(700)
                if await popover_is_open():
                    await self.page.evaluate(
                        """(payload) => { window.__telemostLastParticipantMoreButtonRect = payload; }""",
                        {
                            "x": row_box["x"] + row_box["width"] - 20,
                            "y": y,
                            "left": row_box["x"],
                            "top": row_box["y"],
                            "right": row_box["x"] + row_box["width"],
                            "bottom": row_box["y"] + row_box["height"],
                            "participantText": participant_name,
                        },
                    )
                    return f"clicked participant actions via {dom_result}: {participant_name}; popover_opened=True"

                errors.append(f"{participant_name}: {dom_result}; popover_opened=False")
                await self.page.keyboard.press("Escape")
                await self.page.wait_for_timeout(250)
            except Exception as error:
                errors.append(f"{participant_name}: {error}")
                try:
                    await self.page.keyboard.press("Escape")
                except Exception:
                    pass

        return "participant actions menu not opened; " + " | ".join(errors[-5:])

    async def _click_participants_button(self) -> str:
        result = await self.page.evaluate("""() => {
            const isVisibleAndEnabled = (element) => {
                if (!element) {
                    return false;
                }
                const rect = element.getBoundingClientRect();
                const style = window.getComputedStyle(element);
                return rect.width > 0 && rect.height > 0 &&
                    style.display !== 'none' &&
                    style.visibility !== 'hidden' &&
                    Number(style.opacity || '1') !== 0 &&
                    !element.disabled;
            };

            const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
            const getLabels = (element) => {
                const text = clean(element.innerText || element.textContent);
                const ariaLabel = clean(element.getAttribute('aria-label') || '');
                const title = clean(element.getAttribute('title') || '');
                const testId = clean(element.getAttribute('data-testid') || '');
                const combined = `${text} ${ariaLabel} ${title} ${testId}`.toLowerCase();
                return { text, ariaLabel, title, testId, combined };
            };

            const isParticipantsControl = (element) => {
                const { text, ariaLabel, title, combined } = getLabels(element);
                return (
                    text.toLowerCase().startsWith('\u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u0438') ||
                    ariaLabel.toLowerCase().includes('\u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a') ||
                    title.toLowerCase().includes('\u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a') ||
                    combined.includes('participants')
                );
            };

            const buttons = Array.from(document.querySelectorAll('button, [role="button"]'));
            const participantsButton = buttons.find((button) =>
                isVisibleAndEnabled(button) && isParticipantsControl(button)
            );
            if (!participantsButton) {
                return 'participants button not found';
            }

            const { text, ariaLabel, title, testId } = getLabels(participantsButton);
            participantsButton.click();
            return `clicked participants: ${ariaLabel || title || testId || text || 'unknown'}`;
        }""")
        return result

    async def _participant_display_name(self, row) -> str:
        try:
            display_names = row.locator('span[class*="DisplayName_"]')
            count = await display_names.count()
            if count > 0:
                return " ".join((await display_names.first.inner_text(timeout=1000)).split())
        except Exception:
            pass
        return ""

    def _parse_participant_name_items(self, participants: str) -> list[str]:
        normalized = self._normalize_message_text(participants)
        if not normalized:
            return []

        separators = [",", ";", "\n"]
        parts = [normalized]
        for separator in separators:
            next_parts = []
            for part in parts:
                next_parts.extend(piece.strip() for piece in part.split(separator))
            parts = next_parts

        if len(parts) == 1:
            words = [word for word in parts[0].split() if word]
            if len(words) > 2 and len(words) % 2 == 0:
                parts = [" ".join(words[index:index + 2]) for index in range(0, len(words), 2)]

        result = []
        seen = set()
        for part in parts:
            clean = " ".join(part.split()).strip()
            key = self._normalize_participant_name(clean)
            if clean and key and key not in seen:
                seen.add(key)
                result.append(clean)
        return result

    def _participant_name_from_menu_result(self, menu_result: str) -> str:
        marker = ": "
        suffix = "; popover_opened=True"
        if marker in menu_result and suffix in menu_result:
            return menu_result.rsplit(marker, 1)[-1].split(suffix, 1)[0].strip()
        return ""


    def _parse_protected_participant_names(self, participants: str) -> set[str]:
        normalized = self._normalize_message_text(participants)
        if not normalized:
            return set()

        separators = [",", ";", "\n", " ? "]
        parts = [normalized]
        for separator in separators:
            next_parts = []
            for part in parts:
                next_parts.extend(piece.strip() for piece in part.split(separator))
            parts = next_parts

        if len(parts) == 1:
            words = [word for word in parts[0].split() if word]
            if len(words) > 2 and len(words) % 2 == 0:
                parts = [" ".join(words[index:index + 2]) for index in range(0, len(words), 2)]

        names = {self._normalize_participant_name(part) for part in parts if self._normalize_participant_name(part)}
        names.add(self._normalize_participant_name(normalized))
        return names

    def _normalize_participant_name(self, value: str) -> str:
        return " ".join(str(value).replace("?", "?").replace("?", "?").lower().split()).strip()

    def _is_exit_bot_command(self, text: str) -> bool:
        normalized = self._normalize_message_text(text).lower()
        return normalized in self.EXIT_BOT_COMMANDS

    def _exit_bot_response(self) -> str:
        return "\u041f\u0440\u0438\u043d\u044f\u043b \u043a\u043e\u043c\u0430\u043d\u0434\u0443. \u0417\u0430\u0432\u0435\u0440\u0448\u0430\u044e \u0437\u0430\u043f\u0438\u0441\u044c \u0438 \u0432\u044b\u0445\u043e\u0436\u0443 \u0438\u0437 \u0432\u0441\u0442\u0440\u0435\u0447\u0438."

    def _parse_confidential_command(self, text: str) -> dict | None:
        normalized = self._normalize_message_text(text).lower()
        original = self._normalize_message_text(text)

        for prefix in self.CONFIDENTIAL_NO_RECORDING_PREFIXES:
            if normalized.startswith(prefix):
                participants = original[len(prefix):].strip()
                return {"mode": "no_recording", "participants": participants} if participants else None

        for prefix in self.CONFIDENTIAL_PREFIXES:
            if normalized.startswith(prefix):
                participants = original[len(prefix):].strip()
                return {"mode": "recording", "participants": participants} if participants else None
        return None

    def _confidential_response(self, participants: str, mode: str = "recording") -> str:
        if mode == "no_recording":
            return (
                "\u041a\u043e\u043d\u0444\u0438\u0434\u0435\u043d\u0446\u0438\u0430\u043b\u044c\u043d\u044b\u0439 \u0440\u0435\u0436\u0438\u043c \u0431\u0435\u0437 \u0437\u0430\u043f\u0438\u0441\u0438 \u0432\u043a\u043b\u044e\u0447\u0435\u043d \u0434\u043b\u044f: "
                f"{participants}. "
                "\u0423\u0434\u0430\u043b\u044f\u044e \u043e\u0441\u0442\u0430\u043b\u044c\u043d\u044b\u0445 \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u043e\u0432, \u043e\u0441\u0442\u0430\u043d\u0430\u0432\u043b\u0438\u0432\u0430\u044e \u0437\u0430\u043f\u0438\u0441\u044c \u0438 \u0432\u044b\u0445\u043e\u0436\u0443 \u0438\u0437 \u0432\u0441\u0442\u0440\u0435\u0447\u0438."
            )

        return (
            "\u041a\u043e\u043d\u0444\u0438\u0434\u0435\u043d\u0446\u0438\u0430\u043b\u044c\u043d\u044b\u0439 "
            "\u0440\u0435\u0436\u0438\u043c \u0432\u043a\u043b\u044e\u0447\u0435\u043d \u0434\u043b\u044f: "
            f"{participants}. "
            "\u041f\u0440\u0438\u0441\u0442\u0443\u043f\u0430\u044e \u043a \u0443\u0434\u0430\u043b\u0435\u043d\u0438\u044e "
            "\u043e\u0441\u0442\u0430\u043b\u044c\u043d\u044b\u0445 \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u043e\u0432 "
            "\u0438 \u0437\u0430\u043f\u0438\u0441\u0438 \u043d\u043e\u0432\u043e\u0433\u043e "
            "\u0430\u0443\u0434\u0438\u043e\u043f\u0440\u043e\u0442\u043e\u043a\u043e\u043b\u0430."
        )

    def _find_chat_frame(self):
        for frame in self.page.frames:
            if "yandex.ru/chat" in frame.url:
                return frame
        return None

    def _write_debug_snapshot(self, debug_data: dict) -> None:
        record = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "bot_id": self.bot_id,
            "debug": debug_data,
        }
        self._debug_path().write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    def _debug_path(self) -> Path:
        return Path.cwd() / f"chat_commands_debug_{self.bot_id}.json"

    def _append_new_messages(self, messages: list[dict]) -> list[dict]:
        messages = self._messages_after_current_session_anchor(messages)
        new_records = []
        new_messages = []
        occurrence_counts = {}
        for message in messages:
            if self._is_own_service_message(message):
                continue
            key = self._message_key(message, occurrence_counts)
            if key in self._seen_message_keys:
                continue
            self._seen_message_keys.add(key)
            message["_message_key"] = key
            new_messages.append(message)
            new_records.append(
                {
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                    "bot_id": self.bot_id,
                    "message_key": key,
                    "message": message,
                }
            )

        if not new_records:
            return new_messages

        path = self._messages_path()
        with path.open("a", encoding="utf-8") as file_obj:
            for record in new_records:
                file_obj.write(json.dumps(record, ensure_ascii=False) + "\n")
        return new_messages

    def _messages_after_current_session_anchor(self, messages: list[dict]) -> list[dict]:
        if not self._startup_anchor_text:
            return messages
        if not self._startup_anchor_sent:
            self._mark_messages_as_old(messages)
            self.logger(
                f"[Bot] Chat command session anchor is not confirmed; ignored {len(messages)} visible message(s)"
            )
            return []

        anchor_index = None
        for index, message in enumerate(messages):
            text = self._normalize_message_text(str(message.get("text", "")))
            if text == self._startup_anchor_text:
                anchor_index = index

        if anchor_index is not None:
            self._mark_messages_as_old(messages[: anchor_index + 1])
            if not self._startup_anchor_seen:
                self.logger(
                    f"[Bot] Chat command session anchor found; ignored {anchor_index + 1} message(s) before current session"
                )
            self._startup_anchor_seen = True
            return messages[anchor_index + 1 :]

        if not self._startup_anchor_seen:
            self._startup_anchor_wait_scans += 1
            self._mark_messages_as_old(messages)
            self.logger(
                f"[Bot] Chat command session anchor not visible yet; ignored {len(messages)} visible message(s)"
            )
            return []

        return messages

    def _mark_messages_as_old(self, messages: list[dict]) -> None:
        for message in messages:
            if self._is_own_service_message(message):
                continue
            key = self._message_key(message)
            self._seen_message_keys.add(key)
            self._handled_command_keys.add(key)

    def _is_own_service_message(self, message: dict) -> bool:
        text = self._normalize_message_text(str(message.get("text", "")))
        return bool(text and text in self._bot_sent_texts)

    def _normalize_message_text(self, text: str) -> str:
        return " ".join(text.split()).strip()

    def _cleanup_debug_artifacts(self) -> None:
        if os.getenv("TELEMOST_CHAT_COMMANDS_CLEANUP_DEBUG_FILES", "True") != "True":
            return

        patterns = [
            f"chat_commands_debug_{self.bot_id}.json",
            f"chat_commands_messages_{self.bot_id}.jsonl",
            f"chat_commands_probe_*_{self.bot_id}_*.png",
        ]
        for pattern in patterns:
            for artifact in Path.cwd().glob(pattern):
                try:
                    artifact.unlink()
                    self.logger(f"[Bot] Removed chat debug artifact: {artifact}")
                except FileNotFoundError:
                    pass
                except Exception as error:
                    self.logger(f"[Bot] Could not remove chat debug artifact {artifact}: {error}")

    def _message_key(self, message: dict, occurrence_counts: dict[str, int] | None = None) -> str:
        base_key = "|".join(
            [
                str(message.get("author", "")),
                str(message.get("text", "")),
                str(message.get("time", "")),
            ]
        )
        if occurrence_counts is None:
            return base_key
        occurrence_index = occurrence_counts.get(base_key, 0)
        occurrence_counts[base_key] = occurrence_index + 1
        return f"{base_key}|#{occurrence_index}"

    def _messages_path(self) -> Path:
        return Path.cwd() / f"chat_commands_messages_{self.bot_id}.jsonl"

    def _screenshot_path(self, stage: str) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"chat_commands_probe_{stage}_{self.bot_id}_{timestamp}.png"
        return Path.cwd() / filename
