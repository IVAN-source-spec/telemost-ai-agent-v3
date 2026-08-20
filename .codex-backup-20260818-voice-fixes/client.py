import asyncio
import builtins
import json
import os
from urllib.parse import urlparse
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright
from core.meeting_runtime.participant_policy import should_leave
from core.recording.audio_recorder import AudioRecorder
from pathlib import Path
from datetime import datetime, timezone
from core.constants import USER_AGENT, VIEWPORT, CHROMIUM_ARGS
from core.storage.meeting_storage import MeetingArtifacts, get_meeting_storage
from core.browser_bot.chat_commands import ChatCommandsModule
from core.browser_bot.participants_snapshot import ParticipantsSnapshotModule
from core.browser_bot.participants_summary import ParticipantsSummaryBuilder
from core.browser_bot.agenda_tracker import AgendaTracker
from core.browser_bot.voice_stream import VoiceCommandsAudioClient

DEFAULT_STORAGE_STATE_PATH = "data/auth/yandex-session.json"
DEFAULT_COOKIES_PATH = "data/auth/cookies.json"
DEFAULT_ARTIFACT_PATH = "auth_state.artifact.json"
DEFAULT_PROFILE_DIR = ".telemost-browser-profile"
COMPOSITOR_SCRIPT_PATH = Path(__file__).resolve().parent / "assets" / "compositor.js"


class TelemostBot:
    VOICE_COMMANDS_WITHOUT_AGENDA = frozenset({
        "#описание команд",
        "#выход бота",
    })

    def __init__(
            self,
            headless: bool = False,
            auth_state_path: str | Path | None = None,
            auth_artifact_path: str | Path | None = None,
            profile_dir: str | Path | None = None,
            bot_id: str | None = None,
    ):
        self.headless = headless
        self.bot_id = bot_id or "unknown"
        self.page = None
        self.browser = None
        self.context = None
        self._playwright = None
        self.recorder = None
        self.session_id = None
        self.meeting_title = None
        self.agenda_text = None
        self.expected_participants_text = None
        self.agenda_tracker = None
        self._agenda_activation_lock = asyncio.Lock()
        self.meeting_artifacts: MeetingArtifacts | None = None
        self.meeting_started_at = None
        self.meeting_ended_at = None
        self.meeting_duration_seconds = 0
        self.recording_audio_path: Path | None = None
        self.confidential_dir: Path | None = None
        self.confidential_started_at = None
        self.confidential_ended_at = None
        self.confidential_duration_seconds = 0
        self.confidential_participants = None
        self.confidential_max_participants = 0
        self.confidential_recording_active = False
        self.confidential_no_recording_leave_requested = False
        self.chat_exit_requested = False
        self.participants_snapshot_module = None
        self.confidential_participants_snapshot_module = None
        self.reconnect_events = []
        self.auth_ok = False
        self._last_valid_participant_count = 0
        self.chat_commands_module = None
        self.voice_commands_client = None
        self.voice_service_status = "disabled"
        self.voice_service_session_id = None
        self.voice_service_vad_enabled = False
        self.voice_command_catalog = None
        self._voice_status_loop = None
        self._voice_status_chat_messages_sent: set[str] = set()
        self._pending_voice_status_chat_messages: list[tuple[str, str]] = []
        self._pending_voice_command_payloads: list[dict] = []
        self._pending_agenda_activation_announcement: dict | None = None
        self.auth_state_path = Path(
            auth_state_path
            or os.getenv("TELEMOST_AUTH_STATE_PATH", DEFAULT_STORAGE_STATE_PATH)
        )
        self.auth_cookies_path = Path(os.getenv("TELEMOST_AUTH_COOKIES_PATH", DEFAULT_COOKIES_PATH))
        self.auth_artifact_path = Path(
            auth_artifact_path
            or os.getenv("TELEMOST_SESSION_ARTIFACT_PATH", DEFAULT_ARTIFACT_PATH)
        )
        self.require_auth = os.getenv("TELEMOST_REQUIRE_AUTH", "1").lower() in (
            "1",
            "true",
            "yes",
        )
        profile_dir_value = profile_dir or os.getenv("TELEMOST_BROWSER_PROFILE_DIR", "")
        self.profile_dir = Path(profile_dir_value) if profile_dir_value else None
        self._profile_dir_explicit = profile_dir is not None
        self.join_as_guest = os.getenv("TELEMOST_JOIN_AS_GUEST", "0").lower() in (
            "1",
            "true",
            "yes",
        )
        self.display_name = os.getenv("TELEMOST_DISPLAY_NAME", "Recording Bot")

    def _print(self, *args, **kwargs) -> None:
        if args and isinstance(args[0], str):
            args = (args[0].replace("[Bot]", f"[Bot:{self.bot_id}]", 1), *args[1:])
        builtins.print(*args, **kwargs)

    def _resolve_storage_state_path(self) -> Path | None:
        if self.auth_state_path.exists():
            return self.auth_state_path
        legacy_path = Path("auth_state.json")
        if legacy_path.exists():
            return legacy_path
        return None

    async def _load_yandex_cookies(self) -> bool:
        if not self.context or not self.auth_cookies_path.exists():
            return False
        try:
            payload = json.loads(self.auth_cookies_path.read_text(encoding="utf-8"))
            cookies = payload.get("cookies", [])
            if not cookies:
                return False
            await self.context.add_cookies(cookies)
            self._print(f"[Bot] Loaded Yandex cookies fallback: {self.auth_cookies_path} ({len(cookies)} cookies)")
            return True
        except Exception as error:
            self._print(f"[Bot] Failed to load Yandex cookies fallback: {error}")
            return False

    async def _enter_guest_name(self) -> None:
        selectors = [
            'input[type="text"]',
            'input[data-testid*="guest"]',
            'input[placeholder*="name" i]',
            'input[placeholder*="\u0438\u043c\u044f" i]',
            '.guestNameInput input',
            '[class*="guestName"] input',
        ]
        for selector in selectors:
            try:
                field = await self.page.wait_for_selector(selector, timeout=5000)
                if field and await field.is_visible():
                    await field.fill(self.display_name)
                    self._print(f"[Bot] Entered guest name: {self.display_name}")
                    await self.page.wait_for_timeout(1000)
                    return
            except Exception:
                continue
        self._print("[Bot] Guest name input not found or not needed")

    async def _click_continue_button_legacy(self, meeting_url: str) -> None:
        try:
            # Способ 2: по точному тексту через XPath
            await self.page.click('xpath=//button[contains(text(), "Продолжить в браузере")]', timeout=30000)
            self._print("[Bot] Clicked 'Продолжить в браузере' via XPath")
            await self.page.wait_for_selector('button:has-text("Подключиться")', timeout=10000)
        except Exception as e2:
            self._print(f"[Bot] XPath method failed: {e2}")
            try:
                # Способ 3: JavaScript перебор кнопок
                result = await self.page.evaluate('''() => {
                    const buttons = document.querySelectorAll('button');
                    for (let btn of buttons) {
                        const text = btn.innerText.trim();
                        const dataTestId = btn.getAttribute('data-testid') || '';
                        if (text.includes('Продолжить') || dataTestId === 'orb-button') {
                            btn.click();
                            return 'clicked via JS: ' + (text || dataTestId);
                        }
                    }
                    return 'not found';
                }''')
                self._print(f"[Bot] 'Продолжить в браузере' result: {result}")
                await self.page.wait_for_selector('button:has-text("Подключиться")', timeout=10000)
            except Exception as e3:
                self._print(f"[Bot] All methods failed: {e3}")

    async def _click_continue_button(self, meeting_url: str) -> None:
        try:
            result = await self.page.evaluate('''() => {
                const buttons = document.querySelectorAll('button');
                for (let btn of buttons) {
                    const text = btn.innerText.trim();
                    const dataTestId = btn.getAttribute('data-testid') || '';
                    if (text.includes('Продолжить') || dataTestId === 'orb-button') {
                        btn.click();
                        return 'clicked via JS: ' + (text || dataTestId);
                    }
                }
                return 'not found';
            }''')
            self._print(f"[Bot] 'Продолжить в браузере' result: {result}")
            if result == "not found":
                raise RuntimeError("Continue button not found via JS")
            await self.page.wait_for_selector('button:has-text("Подключиться")', timeout=10000)
            return
        except Exception as e1:
            self._print(f"[Bot] JS method failed: {e1}")

        try:
            await self.page.click(
                'xpath=//button[contains(text(), "Продолжить в браузере")]',
                timeout=5000,
            )
            self._print("[Bot] Clicked 'Продолжить в браузере' via XPath")
            await self.page.wait_for_selector('button:has-text("Подключиться")', timeout=10000)
        except Exception as e2:
            self._print(f"[Bot] All methods failed: {e2}")

    async def _click_ai_projects_join_button(self) -> str:
        for _ in range(60):
            result = await self.page.evaluate(
                """() => {
                    const candidates = Array.from(document.querySelectorAll('button'));
                    const isVisible = (node) => {
                        const style = window.getComputedStyle(node);
                        const rect = node.getBoundingClientRect();
                        return style.visibility !== 'hidden' &&
                            style.display !== 'none' &&
                            rect.width > 0 &&
                            rect.height > 0;
                    };
                    for (const button of candidates) {
                        const text = (button.innerText || button.textContent || '').trim();
                        const dataTestId = button.getAttribute('data-testid') || '';
                        const className = String(button.className || '');
                        const type = button.getAttribute('type') || '';
                        const looksLikeJoin =
                            dataTestId.includes('join') ||
                            text.includes('Войти') ||
                            text.includes('Присоединиться') ||
                            text.includes('Подключиться') ||
                            text.includes('Join') ||
                            className.includes('joinButton') ||
                            className.includes('JoinButton') ||
                            type === 'submit';
                        if (looksLikeJoin && isVisible(button) && !button.disabled) {
                            button.scrollIntoView({ block: 'center', inline: 'center' });
                            button.click();
                            return `clicked: ${text || dataTestId || type || className}`;
                        }
                    }
                    return 'not ready';
                }"""
            )
            if result != "not ready":
                return result
            await self.page.wait_for_timeout(1000)
        return "join button not found"

    async def _mute_microphone_js(self):
        try:
            result = await self.page.evaluate('''() => {
                const selectors = [
                    '[data-testid="mute-audio"]',
                    '[data-testid="turn-off-mic-button"]',
                    'button[aria-label*="микрофон"]',
                    'button[aria-label*="Microphone"]'
                ];
                for (let sel of selectors) {
                    const btn = document.querySelector(sel);
                    if (btn) {
                        btn.click();
                        return 'clicked: ' + sel;
                    }
                }
                const buttons = document.querySelectorAll('button');
                for (let btn of buttons) {
                    const aria = btn.getAttribute('aria-label') || '';
                    const dataTestId = btn.getAttribute('data-testid') || '';
                    if (aria.includes('микрофон') || aria.includes('Microphone') || 
                        dataTestId.includes('mic') || dataTestId.includes('mute') || dataTestId.includes('audio')) {
                        btn.click();
                        return 'clicked fallback: ' + (aria || dataTestId);
                    }
                }
                return 'not found';
            }''')
            self._print(f"[Bot] Microphone toggle result: {result}")
        except Exception as e:
            self._print(f"[Bot] Mute error: {e}")

    async def join(self, meeting_url: str, session_id: str = None):
        self.session_id = session_id

        if self._playwright is None:
            self._playwright = await async_playwright().start()
        if self.browser is not None and not self.browser.is_connected():
            self.browser = None
            self.context = None
            self.page = None
        context_options = {
            "permissions": ["camera", "microphone"],
            "user_agent": USER_AGENT,
            "viewport": VIEWPORT,
        }

        if self.profile_dir is not None and self.browser is None:
            self._print(
                "[Bot] TELEMOST_BROWSER_PROFILE_DIR is ignored: "
                "AIProjects-style auth uses storage_state, not a persistent profile"
            )

        if self.browser is None:
            self.browser = await self._playwright.chromium.launch(
                headless=self.headless,
                args=CHROMIUM_ARGS,
            )

        if self.context is None:
            storage_state_path = None if self.join_as_guest else self._resolve_storage_state_path()
            if self.join_as_guest:
                self.auth_ok = False
                self._print("[Bot] AIProjects guest mode enabled: Yandex storage_state is not loaded for meeting join")
            elif storage_state_path is not None:
                context_options["storage_state"] = str(storage_state_path)
                self.auth_ok = True
                self._print(f"[Bot] Loaded AIProjects-style storage_state: {storage_state_path}")
            else:
                self.auth_ok = False
                self._print(f"[Bot] Storage state not found: {self.auth_state_path}")

            self.context = await self.browser.new_context(**context_options)

            if not self.join_as_guest and not self.auth_ok:
                self.auth_ok = await self._load_yandex_cookies()

            if not self.join_as_guest and not self.auth_ok:
                message = "Authorized Yandex session unavailable"
                if self.require_auth:
                    raise RuntimeError(message)
                self._print(f"[Bot] {message}; using guest mode")
            await self._install_timer_camera()

        if self.page is None or self.page.is_closed():
            self.page = await self.context.new_page()
        parsed_meeting_url = urlparse(meeting_url)
        meeting_origin = f"{parsed_meeting_url.scheme}://{parsed_meeting_url.netloc}"
        await self.context.grant_permissions(["camera", "microphone"], origin=meeting_origin)

        await self.page.goto(meeting_url, wait_until="domcontentloaded", timeout=60000)
        self._print("[Bot] Navigated to meeting page")
        try:
            await self.page.wait_for_selector(
                '.spinnerContainer_dP9Pg, [data-testid="orb-spinner"]',
                state="hidden",
                timeout=30000,
            )
        except Exception:
            pass

        await self.page.wait_for_timeout(3000)
        await self._click_continue_button(meeting_url)

        if not self.join_as_guest:
            self._print("[Bot] Joining as authenticated user")
        else:
            await self._enter_guest_name()

        # === НАЖИМАЕМ "ПОДКЛЮЧИТЬСЯ" (радикальное решение) ===
        try:
            result = await self._click_ai_projects_join_button()
            self._print(f"[Bot] JS click result: {result}")
            await self.page.wait_for_timeout(5000)

            # Ждём изменения URL (признак входа)
            try:
                await self.page.wait_for_function(
                    '''() => {
                        const url = window.location.href;
                        const meetingUi =
                            !!document.querySelector('[data-testid="participant-item"]') ||
                            !!document.querySelector('video') ||
                            !!document.querySelector('[class*="participant"]');
                        return (
                            meetingUi ||
                            (url.includes('telemost.yandex') && url.includes('/j/'))
                        );
                    }''',
                    timeout=15000
                )
                self._print("[Bot] URL changed, meeting joined!")
            except:
                self._print("[Bot] URL did not change, checking page content...")

                # Проверяем, не появилось ли что-то новое на странице
                content = await self.page.evaluate('''() => {
                    return {
                        text: document.body.innerText,
                        hasVideo: !!document.querySelector('video'),
                        hasParticipants: !!document.querySelector('[data-testid="participant-item"]')
                    };
                }''')
                self._print(f"[Bot] Page content: {content}")

        except Exception as e:
            self._print(f"[Bot] Could not click 'Подключиться': {e}")

        # Даём время на загрузку интерфейса встречи
        # await asyncio.sleep(3)

        try:
            await self.page.wait_for_selector('[data-testid="participant-item"], video, [class*="participant"]',
                                              timeout=5000)
            self._print("[Bot] Meeting page detected")
        except:
            self._print("[Bot] Meeting page not detected, but continuing")

        self._start_meeting_timer()
        self._start_agenda_tracker_if_enabled()
        await self._push_timer_camera_state()
        await self._mute_microphone_js()

        self._start_recording()
        await self._start_participants_snapshot_if_enabled()
        asyncio.create_task(self._run_chat_commands_probe_if_enabled())



    def _start_agenda_tracker_if_enabled(self) -> None:
        if not self.agenda_text:
            self.agenda_tracker = None
            return
        result = self._activate_agenda_text(self.agenda_text, source="initial")
        if result.get("status") == "invalid_agenda":
            self._print(f"[Bot] Agenda was provided but is invalid: {result.get('error')}")
        elif result.get("status") == "failed":
            self._print(f"[Bot] Agenda tracker start failed: {result.get('error')}")

    def _activate_agenda_text(self, raw_agenda: str, source: str) -> dict:
        if self.agenda_tracker is not None and self.agenda_tracker.enabled:
            return {
                "status": "already_active",
                "source": getattr(self.agenda_tracker, "activation_source", "unknown"),
                "items_count": len(self.agenda_tracker.items),
            }
        if self.meeting_started_at is None:
            return {"status": "failed", "error": "meeting timer is not started"}

        items, validation_error = AgendaTracker.validate_agenda(raw_agenda)
        if validation_error:
            return {"status": "invalid_agenda", "error": validation_error}

        try:
            tracker = AgendaTracker(
                raw_agenda=raw_agenda,
                meeting_dir=self._meeting_dir(),
                meeting_started_at=self.meeting_started_at,
                logger=self._print,
                bot_id=self.bot_id,
                activation_source=source,
            )
            if not tracker.enabled:
                return {"status": "invalid_agenda", "error": "не удалось распознать пункты повестки"}
            self.agenda_text = raw_agenda
            self.agenda_tracker = tracker
            self.agenda_tracker.start()
            self._print(f"[Bot] Agenda activated from {source}: {len(items)} item(s)")
            return {"status": "agenda_activated", "source": source, "items_count": len(items)}
        except Exception as error:
            self.agenda_tracker = None
            return {"status": "failed", "error": str(error)}

    async def _handle_agenda_event(self, stage: str, **payload) -> dict | None:
        if stage == "activate_agenda":
            async with self._agenda_activation_lock:
                result = self._activate_agenda_text(
                    str(payload.get("raw_agenda") or ""),
                    source=str(payload.get("source") or "unknown"),
                )
            if result.get("status") == "agenda_activated":
                await self._push_timer_camera_state()
            return result

        if stage == "agenda_commands_announced":
            return await self._announce_agenda_voice_commands()

        if self.agenda_tracker is None:
            return {"status": "disabled"}

        if stage == "next_question":
            result = self.agenda_tracker.next_question()
        elif stage == "switch_question":
            result = self.agenda_tracker.switch_to_question(int(payload.get("question_number") or 0))
        elif stage == "end_question":
            result = self.agenda_tracker.end_question()
        elif stage == "questions_without_time":
            result = self.agenda_tracker.agenda_items_without_time()
        elif stage == "unfinished_questions":
            result = self.agenda_tracker.unfinished_questions()
        elif stage == "all_questions":
            result = self.agenda_tracker.all_questions()
        elif stage == "assign_time":
            result = self.agenda_tracker.assign_question_time(
                int(payload.get("question_number") or 0),
                str(payload.get("raw_time") or ""),
            )
        elif stage == "add_question":
            result = self.agenda_tracker.add_question(str(payload.get("question_text") or ""))
        elif stage == "skip_current_question":
            result = self.agenda_tracker.skip_current_question()
        elif stage == "skip_question":
            result = self.agenda_tracker.skip_question(int(payload.get("question_number") or 0))
        else:
            result = {"status": "disabled"}

        await self._push_timer_camera_state()
        return result

    def agenda_control_status(self) -> dict:
        tracker = self.agenda_tracker
        agenda_active = tracker is not None and tracker.enabled
        return {
            "bot_id": self.bot_id,
            "session_id": self.session_id,
            "meeting_title": self.meeting_title,
            "meeting_started": self.meeting_started_at is not None,
            "agenda_active": bool(agenda_active),
            "source": getattr(tracker, "activation_source", None) if agenda_active else None,
            "activated_at": getattr(tracker, "activated_at", None) if agenda_active else None,
            "items_count": len(tracker.items) if agenda_active else 0,
        }

    async def activate_agenda_from_external(
        self,
        raw_agenda: str,
        source: str = "calendar_monitor",
        metadata: dict | None = None,
    ) -> dict:
        result = await self._handle_agenda_event(
            "activate_agenda",
            raw_agenda=raw_agenda,
            source=source,
            **(metadata or {}),
        )
        result = dict(result or {})
        if result.get("status") == "agenda_activated":
            announcement = await self._announce_dynamic_agenda_activation(result, source=source)
            result.update(announcement)
        return result

    async def _announce_agenda_voice_commands(self) -> dict:
        if self.agenda_tracker is None or not self.agenda_tracker.enabled:
            return {"status": "disabled"}
        await self._send_voice_status_chat_message(
            "voice_agenda_commands_available",
            self._format_voice_command_catalog_message({}),
        )
        return {"status": "voice_commands_announced"}

    async def _announce_dynamic_agenda_activation(self, agenda_result: dict, source: str) -> dict:
        if self.chat_commands_module is None:
            self._pending_agenda_activation_announcement = dict(agenda_result)
            return {"chat_commands_message": "pending", "voice_commands": "pending"}

        self.chat_commands_module.agenda_enabled = True
        if hasattr(self.chat_commands_module, "_clear_agenda_submission_wait"):
            self.chat_commands_module._clear_agenda_submission_wait()
        response = self.chat_commands_module._agenda_activated_commands_text(agenda_result)
        chat_result = await self.chat_commands_module._send_service_message(response)
        voice_result = await self._announce_agenda_voice_commands()
        self._print(f"[Bot] Agenda activation announcement from {source}: {chat_result}; voice={voice_result}")
        return {
            "chat_commands_message": chat_result,
            "voice_commands": voice_result,
        }

    def _participants_snapshot_self_name_markers(self) -> list[str]:
        return [self.display_name, "\u0412\u0435\u0440\u0442\u0435\u0440 \u0420\u043e\u0431\u043e\u0442", "Telemost Bot"]

    async def _start_participants_snapshot_if_enabled(self) -> None:
        if os.getenv("TELEMOST_PARTICIPANTS_SNAPSHOT_ENABLED", "True") != "True":
            return
        if self.meeting_started_at is None:
            return
        try:
            self.participants_snapshot_module = ParticipantsSnapshotModule(
                page=self.page,
                meeting_dir=self._meeting_dir(),
                meeting_started_at=self.meeting_started_at,
                logger=self._print,
                bot_id=self.bot_id,
                self_name_markers=self._participants_snapshot_self_name_markers(),
            )
            self.participants_snapshot_module.start_initial_snapshot()
        except Exception as error:
            self._print(f"[Bot] Participants snapshot start failed: {error}")

    async def _start_confidential_participants_snapshot_if_enabled(self) -> None:
        if os.getenv("TELEMOST_PARTICIPANTS_SNAPSHOT_ENABLED", "True") != "True":
            return
        if self.confidential_started_at is None or self.confidential_dir is None:
            return
        try:
            if self.participants_snapshot_module is not None:
                self.participants_snapshot_module.stop()
            self.confidential_participants_snapshot_module = ParticipantsSnapshotModule(
                page=self.page,
                meeting_dir=self.confidential_dir,
                meeting_started_at=self.confidential_started_at,
                logger=self._print,
                bot_id=self.bot_id,
                self_name_markers=self._participants_snapshot_self_name_markers(),
            )
            if self.confidential_participants:
                self.confidential_participants_snapshot_module.append_known_participants_snapshot(
                    self._split_confidential_participants(self.confidential_participants)
                )
            self.confidential_participants_snapshot_module.start_initial_snapshot()
        except Exception as error:
            self._print(f"[Bot] Confidential participants snapshot start failed: {error}")

    def _active_participants_snapshot_module(self):
        if self.confidential_recording_active and self.confidential_participants_snapshot_module is not None:
            return self.confidential_participants_snapshot_module
        return self.participants_snapshot_module

    def _handle_expected_participants_event(self, participants: list[dict]) -> None:
        self.expected_participants_text = list(participants)
        self._print(f"[Bot] Expected participants updated: {len(participants)}")

    async def _run_chat_commands_probe_if_enabled(self) -> None:
        if os.getenv("TELEMOST_CHAT_COMMANDS_ENABLED", "False") != "True":
            return
        try:
            module = ChatCommandsModule(
                page=self.page,
                logger=self._print,
                bot_id=self.bot_id,
                confidential_event_handler=self._handle_confidential_mode_event,
                agenda_event_handler=self._handle_agenda_event,
                agenda_enabled=self.agenda_tracker is not None and self.agenda_tracker.enabled,
                expected_participants=self.expected_participants_text,
                expected_participants_event_handler=self._handle_expected_participants_event,
            )
            self.chat_commands_module = module
            await module.run_probe()
            if self._pending_agenda_activation_announcement is not None:
                pending_agenda = self._pending_agenda_activation_announcement
                self._pending_agenda_activation_announcement = None
                await self._announce_dynamic_agenda_activation(pending_agenda, source=pending_agenda.get("source") or "unknown")
            await self._flush_pending_voice_status_chat_messages()
            await self._flush_pending_voice_commands()
        except Exception as error:
            self._print(f"[Bot] Chat commands probe failed: {error}")

    def _meeting_dir(self) -> Path:
        return self._meeting_artifacts().meeting_dir

    def _meeting_artifacts(self) -> MeetingArtifacts:
        if self.meeting_artifacts is not None:
            return self.meeting_artifacts
        if not self.session_id:
            raise RuntimeError("session_id is required to save meeting artifacts")
        if self.meeting_started_at is None:
            raise RuntimeError("meeting_started_at is required to save meeting artifacts")
        self.meeting_artifacts = get_meeting_storage().prepare_meeting(
            session_id=self.session_id,
            title=self.meeting_title,
            started_at_utc=self.meeting_started_at,
        )
        return self.meeting_artifacts

    async def _install_timer_camera(self) -> None:
        if not COMPOSITOR_SCRIPT_PATH.exists():
            raise FileNotFoundError(f"Compositor script not found: {COMPOSITOR_SCRIPT_PATH}")
        script = COMPOSITOR_SCRIPT_PATH.read_text(encoding="utf-8")
        await self.context.add_init_script(script)
        self._print("[Bot] Timer camera compositor installed")

    async def _push_timer_camera_state(self) -> None:
        if not self.page or not self.meeting_started_at:
            return

        start_time_ms = int(self.meeting_started_at.timestamp() * 1000)
        payload = {
            "scene": "timer",
            "meetingTitle": "Telemost Bot",
            "startTimeMs": start_time_ms,
        }
        if self.agenda_tracker is not None:
            payload.update(self.agenda_tracker.overlay_state())

        try:
            has_compositor = await self.page.evaluate("typeof window.__COMPOSITOR__ !== 'undefined'")
            if not has_compositor:
                script = COMPOSITOR_SCRIPT_PATH.read_text(encoding="utf-8")
                await self.page.evaluate(script)
                self._print("[Bot] Timer camera compositor reinjected")
            await self.page.evaluate(
                "(data) => window.__COMPOSITOR__ && window.__COMPOSITOR__.updateScene(data)",
                payload,
            )
            self._print("[Bot] Timer camera state updated")
        except Exception as e:
            self._print(f"[Bot] Timer camera update failed: {e}")

    @staticmethod
    def _format_duration(total_seconds: int) -> str:
        safe_seconds = max(0, int(total_seconds))
        hours = safe_seconds // 3600
        minutes = (safe_seconds % 3600) // 60
        seconds = safe_seconds % 60
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def _start_meeting_timer(self) -> None:
        if self.meeting_started_at is not None:
            return
        self.meeting_started_at = datetime.now(timezone.utc)
        self.meeting_ended_at = None
        self.meeting_duration_seconds = 0
        self._write_meeting_time()
        self._print(f"[Bot] Meeting timer started at {self.meeting_started_at.isoformat()}")

    def _finish_meeting_timer(self) -> None:
        if self.meeting_started_at is None:
            return
        self.meeting_ended_at = datetime.now(timezone.utc)
        self.meeting_duration_seconds = int(
            (self.meeting_ended_at - self.meeting_started_at).total_seconds()
        )
        self._write_meeting_time()
        self._print(
            "[Bot] Meeting duration: "
            f"{self._format_duration(self.meeting_duration_seconds)}"
        )

    def _write_meeting_time(self) -> None:
        if self.meeting_started_at is None:
            return
        payload = {
            "session_id": self.session_id,
            "title": self._meeting_artifacts().title,
            "started_at": self.meeting_started_at.isoformat(),
            "started_at_astrakhan": self._meeting_artifacts().started_at_local.isoformat(),
            "ended_at": self.meeting_ended_at.isoformat() if self.meeting_ended_at else None,
            "duration_seconds": self.meeting_duration_seconds,
            "duration_formatted": self._format_duration(self.meeting_duration_seconds),
            "reconnects": self.reconnect_events,
        }
        path = self._meeting_artifacts().meeting_time_path
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _start_recording(self, audio_path: str | Path | None = None):
        """Start audio recording in a background thread."""
        target_path = Path(audio_path) if audio_path else self._meeting_artifacts().audio_path
        if self.recorder is not None:
            self._print(f"[Bot] Audio recording already active: {self.recording_audio_path}")
            return
        self.recording_audio_path = target_path
        self.recording_audio_path.parent.mkdir(parents=True, exist_ok=True)
        on_audio_chunk = None
        if VoiceCommandsAudioClient.enabled():
            try:
                self._voice_status_loop = asyncio.get_running_loop()
            except RuntimeError:
                self._voice_status_loop = None
            self.voice_service_status = "connecting"
            self.voice_commands_client = VoiceCommandsAudioClient(
                node_id=os.getenv("BOT_NODE_ID"),
                bot_id=self.bot_id,
                meeting_id=self.session_id,
                meeting_title=self.meeting_title,
                sample_rate=44100,
                channels=2,
                chunk_size=1024,
                event_handler=self._handle_voice_service_payload_from_thread,
            )
            self.voice_commands_client.start()
            on_audio_chunk = self.voice_commands_client.on_audio_chunk
            self._print("[Bot] Voice command audio stream started")
        self.recorder = AudioRecorder(log_prefix=f"[AudioRecorder:{self.bot_id}]", on_audio_chunk=on_audio_chunk)
        self.recorder.start()
        self._print(f"[Bot] Audio recording started: {self.recording_audio_path}")

    def _stop_recording(self):
        if self.recorder is None:
            return
        self.recorder.stop()
        if self.recording_audio_path is not None:
            filename = self.recording_audio_path
        elif self.session_id:
            filename = self._meeting_artifacts().audio_path
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = Path(f"recording_{timestamp}.wav")
        self.recorder.save(str(filename))
        self.recorder.close()
        self.recorder = None
        if self.voice_commands_client is not None:
            self.voice_commands_client.stop()
            self.voice_commands_client = None
        self.recording_audio_path = None
        self._print(f"[Bot] Audio recording saved to {filename}")

    def _handle_voice_service_payload_from_thread(self, payload: dict) -> None:
        loop = self._voice_status_loop
        if loop is None or loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(self._handle_voice_service_payload(payload), loop)
        except Exception:
            pass

    async def _handle_voice_service_payload(self, payload: dict) -> None:
        payload_type = payload.get("type")
        if payload_type == "voice_service_connected":
            self.voice_service_status = "connected"
            self.voice_service_session_id = payload.get("session_id")
            vad = payload.get("vad") or {}
            self.voice_service_vad_enabled = bool(vad.get("enabled"))
            self._remember_voice_command_catalog(payload)
            self._write_voice_status_debug("voice_service_connected", payload)
            self._print("[Bot] Voice command service connected")
        elif payload_type == "start_accepted":
            self.voice_service_status = "streaming"
            self.voice_service_session_id = payload.get("session_id")
            self._remember_voice_command_catalog(payload)
            self._write_voice_status_debug("voice_service_streaming", payload)
            self._print("[Bot] Voice command audio stream accepted")
        elif payload_type == "vad_event":
            self._write_voice_status_debug(f"vad_{payload.get('event') or 'event'}", payload)
        elif payload_type == "session_stopped":
            self.voice_service_status = "stopped"
            self._write_voice_status_debug("voice_service_stopped", payload)
        elif payload_type == "voice_agent_ready":
            self.voice_service_status = "agent_ready"
            self._remember_voice_command_catalog(payload)
            self._write_voice_status_debug("voice_agent_ready", payload)
            await self._send_voice_status_chat_message(
                "voice_agent_ready",
                self._format_voice_command_catalog_message(payload),
            )
        elif payload_type == "voice_agent_unavailable":
            self.voice_service_status = "agent_unavailable"
            self._write_voice_status_debug("voice_agent_unavailable", payload)
        elif payload_type == "voice_agent_disconnected":
            self.voice_service_status = "agent_reconnecting"
            self._write_voice_status_debug("voice_agent_disconnected", payload)
        elif payload_type == "voice_agent_reconnect_attempt":
            self.voice_service_status = "agent_reconnecting"
            self._write_voice_status_debug("voice_agent_reconnect_attempt", payload)
        elif payload_type in {"voice_agent_recovered", "voice_agent_reconnect_succeeded"}:
            self.voice_service_status = "agent_ready"
            self._remember_voice_command_catalog(payload)
            self._write_voice_status_debug(payload_type, payload)
            if payload_type == "voice_agent_recovered":
                await self._send_voice_status_chat_message(
                    "voice_agent_recovered",
                    "\u0413\u043e\u043b\u043e\u0441\u043e\u0432\u043e\u0439 \u043a\u0430\u043d\u0430\u043b \u0432\u043e\u0441\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d. \u0413\u043e\u043b\u043e\u0441\u043e\u0432\u044b\u0435 \u043a\u043e\u043c\u0430\u043d\u0434\u044b \u0441\u043d\u043e\u0432\u0430 \u0434\u043e\u0441\u0442\u0443\u043f\u043d\u044b.",
                )
        elif payload_type in {"voice_command", "voice_command_detected", "command", "command_detected"}:
            await self._handle_voice_command_payload(payload)
        elif payload_type == "voice_service_unavailable":
            self.voice_service_status = "unavailable"
            self._write_voice_status_debug("voice_service_unavailable", payload)
            self._print(f"[Bot] Voice command service unavailable: {payload.get('error')}")
            await self._send_voice_status_chat_message(
                "voice_service_unavailable",
                "\u0413\u043e\u043b\u043e\u0441\u043e\u0432\u043e\u0439 \u043a\u0430\u043d\u0430\u043b \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u0435\u043d. \u041a\u043e\u043c\u0430\u043d\u0434\u044b \u0432 \u0447\u0430\u0442\u0435 \u0440\u0430\u0431\u043e\u0442\u0430\u044e\u0442 \u0448\u0442\u0430\u0442\u043d\u043e.",
            )

    def _remember_voice_command_catalog(self, payload: dict) -> None:
        catalog = payload.get("command_catalog")
        if isinstance(catalog, dict):
            self.voice_command_catalog = catalog
            self._write_voice_status_debug("voice_commands_catalog_loaded", catalog)
            return
        commands = payload.get("commands")
        if isinstance(commands, list):
            self.voice_command_catalog = {"prefix": "\u0420\u043e\u0431\u043e\u0442 \u0412\u0435\u0440\u0442\u0435\u0440", "commands": commands}
            self._write_voice_status_debug("voice_commands_catalog_loaded", self.voice_command_catalog)

    def _format_voice_command_catalog_message(self, payload: dict) -> str:
        catalog = payload.get("command_catalog") if isinstance(payload.get("command_catalog"), dict) else self.voice_command_catalog
        commands = []
        if isinstance(catalog, dict) and isinstance(catalog.get("commands"), list):
            commands = [str(command).strip() for command in catalog.get("commands") if str(command).strip()]
        elif isinstance(payload.get("commands"), list):
            commands = [str(command).strip() for command in payload.get("commands") if str(command).strip()]

        agenda_active = self.agenda_tracker is not None and self.agenda_tracker.enabled
        if not agenda_active:
            commands = [
                command
                for command in commands
                if " ".join(command.split()).casefold() in self.VOICE_COMMANDS_WITHOUT_AGENDA
            ]

        if not commands:
            return "\u0413\u043e\u043b\u043e\u0441\u043e\u0432\u044b\u0435 \u043a\u043e\u043c\u0430\u043d\u0434\u044b \u0434\u043e\u0441\u0442\u0443\u043f\u043d\u044b.\n\u0421\u043a\u0430\u0436\u0438\u0442\u0435: \u0420\u043e\u0431\u043e\u0442 \u0412\u0435\u0440\u0442\u0435\u0440, \u0437\u0430\u0442\u0435\u043c \u043a\u043e\u043c\u0430\u043d\u0434\u0443."

        prefix = "\u0420\u043e\u0431\u043e\u0442 \u0412\u0435\u0440\u0442\u0435\u0440"
        if isinstance(catalog, dict) and isinstance(catalog.get("prefix"), str) and catalog.get("prefix").strip():
            prefix = catalog["prefix"].strip()

        lines = [
            "\u0413\u043e\u043b\u043e\u0441\u043e\u0432\u044b\u0435 \u043a\u043e\u043c\u0430\u043d\u0434\u044b \u0434\u043e\u0441\u0442\u0443\u043f\u043d\u044b.",
            f"\u0421\u043a\u0430\u0436\u0438\u0442\u0435: {prefix}, \u0437\u0430\u0442\u0435\u043c \u043e\u0434\u043d\u0443 \u0438\u0437 \u043a\u043e\u043c\u0430\u043d\u0434:",
        ]
        lines.extend(commands)
        return "\n".join(lines)

    async def _handle_voice_command_payload(self, payload: dict) -> None:
        command = self._extract_voice_command_text(payload)
        if not command:
            self._write_voice_status_debug("voice_command_ignored", {"reason": "empty command", "payload": payload})
            return
        command = command.strip()
        if command and not command.startswith("#"):
            command = f"#{command}"

        command_id = str(
            payload.get("command_id")
            or payload.get("id")
            or payload.get("received_at")
            or datetime.now(timezone.utc).isoformat()
        )

        if self.chat_commands_module is None:
            pending_payload = dict(payload)
            pending_payload["command"] = command
            pending_payload["command_id"] = command_id
            self._pending_voice_command_payloads.append(pending_payload)
            self._write_voice_status_debug("voice_command_pending", {"command": command, "command_id": command_id})
            return

        handled = await self.chat_commands_module.handle_command_text(
            command,
            source="voice",
            command_id=command_id,
        )
        self._write_voice_status_debug(
            "voice_command_handled" if handled else "voice_command_ignored",
            {"command": command, "command_id": command_id, "payload": payload},
        )

    def _extract_voice_command_text(self, payload: dict) -> str:
        for key in ("command", "normalized_command", "text"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    async def _flush_pending_voice_commands(self) -> None:
        pending = list(self._pending_voice_command_payloads)
        self._pending_voice_command_payloads.clear()
        for payload in pending:
            await self._handle_voice_command_payload(payload)

    async def _send_voice_status_chat_message(self, event_key: str, message: str) -> None:
        if event_key in self._voice_status_chat_messages_sent:
            return
        if self.chat_commands_module is None:
            pending = (event_key, message)
            if pending not in self._pending_voice_status_chat_messages:
                self._pending_voice_status_chat_messages.append(pending)
                self._write_voice_status_debug("voice_status_chat_message_pending", {"type": event_key, "message": message})
            return
        try:
            # The chat iframe may silently drop a message sent immediately after the startup text.
            # Treat the message as sent only after it appears in the visible chat list.
            await self.page.wait_for_timeout(1500)
            last_result = "not attempted"
            for attempt in range(1, 4):
                result = await self.chat_commands_module._send_service_message(message)
                last_result = result
                await self.page.wait_for_timeout(1200)
                if await self._voice_status_chat_message_is_visible(message):
                    self._voice_status_chat_messages_sent.add(event_key)
                    self._write_voice_status_debug("voice_status_chat_message_sent", {
                        "type": event_key,
                        "message": message,
                        "result": result,
                        "attempt": attempt,
                        "verified_visible": True,
                    })
                    self._print(f"[Bot] Voice status chat message result: {result} (verified)")
                    return
                self._write_voice_status_debug("voice_status_chat_message_not_visible", {
                    "type": event_key,
                    "message": message,
                    "result": result,
                    "attempt": attempt,
                })
                await self.page.wait_for_timeout(1500)
            self._write_voice_status_debug("voice_status_chat_message_failed", {
                "type": event_key,
                "message": message,
                "error": "message was not visible after retries",
                "last_result": last_result,
            })
            self._print("[Bot] Voice status chat message was not visible after retries")
        except Exception as error:
            self._write_voice_status_debug("voice_status_chat_message_failed", {"type": event_key, "message": message, "error": str(error)})
            self._print(f"[Bot] Voice status chat message failed: {error}")

    async def _voice_status_chat_message_is_visible(self, message: str) -> bool:
        if self.chat_commands_module is None:
            return False
        expected = self.chat_commands_module._normalize_message_text(message)
        try:
            messages = await self.chat_commands_module._read_visible_chat_messages()
        except Exception as error:
            self._write_voice_status_debug("voice_status_chat_message_verify_failed", {"message": message, "error": str(error)})
            return False
        for item in messages:
            text = self.chat_commands_module._normalize_message_text(str(item.get("text") or ""))
            if text == expected:
                return True
        return False

    async def _flush_pending_voice_status_chat_messages(self) -> None:
        pending = list(self._pending_voice_status_chat_messages)
        self._pending_voice_status_chat_messages.clear()
        for event_key, message in pending:
            await self._send_voice_status_chat_message(event_key, message)

    def _write_voice_status_debug(self, event: str, payload: dict) -> None:
        debug_path = Path(os.getenv("VOICE_COMMANDS_DEBUG_FILE", f"voice_commands_debug_{self.bot_id}.jsonl"))
        record = {
            "written_at": datetime.now(timezone.utc).isoformat(),
            "bot_id": self.bot_id,
            "meeting_id": self.session_id,
            "meeting_title": self.meeting_title,
            "event": event,
            "voice_service_status": self.voice_service_status,
            "payload": payload,
        }
        try:
            with debug_path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass

    async def _handle_confidential_mode_event(self, stage: str, participants: str, mode: str = "recording") -> None:
        if stage == "exit_requested":
            self.chat_exit_requested = True
            self._print("[Bot] Exit requested from chat command")
            return

        if stage == "before_participant_cleanup":
            if self.confidential_started_at is not None or self.confidential_no_recording_leave_requested:
                self._print("[Bot] Confidential mode is already initialized")
                return
            self.confidential_participants = participants
            self._print(f"[Bot] Confidential mode requested for: {participants} ({mode})")
            self._stop_recording()
            self._write_meeting_time()
            return

        if stage == "after_participant_cleanup":
            if mode == "no_recording":
                self.confidential_no_recording_leave_requested = True
                self._print("[Bot] Confidential no-recording mode cleanup finished; leaving meeting")
                return
            if self.confidential_recording_active:
                return
            await self._start_confidential_recording(participants)

    def _split_confidential_participants(self, participants: str) -> list[str]:
        normalized = " ".join(str(participants or "").split()).strip()
        if not normalized:
            return []
        for separator in [",", ";", "\n"]:
            if separator in normalized:
                return [part.strip() for part in normalized.split(separator) if part.strip()]
        words = [word for word in normalized.split() if word]
        if len(words) > 2 and len(words) % 2 == 0:
            return [" ".join(words[index:index + 2]) for index in range(0, len(words), 2)]
        return [normalized]

    async def _start_confidential_recording(self, participants: str) -> None:
        self.confidential_participants = participants
        self.confidential_started_at = datetime.now(timezone.utc)
        self.confidential_ended_at = None
        self.confidential_duration_seconds = 0
        self.confidential_max_participants = 0
        self.confidential_recording_active = True
        self.confidential_dir = self._meeting_dir() / "\u041a\u043e\u043d\u0444\u0438\u0434\u0435\u043d\u0446\u0438\u0430\u043b\u044c\u043d\u0430\u044f \u0447\u0430\u0441\u0442\u044c"
        self.confidential_dir.mkdir(parents=True, exist_ok=True)
        self._write_confidential_meeting_time()
        self._write_confidential_recording_status("recording")
        confidential_audio_path = self.confidential_dir / "recording_meeting.wav"
        if self.recorder is not None:
            self._print("[Bot] Recorder was still active before confidential recording; stopping it first")
            self._stop_recording()
        self._start_recording(confidential_audio_path)
        if self.recording_audio_path != confidential_audio_path:
            raise RuntimeError(f"Confidential recorder did not start at expected path: {confidential_audio_path}")
        await self._start_confidential_participants_snapshot_if_enabled()
        self._print(f"[Bot] Confidential recording started: {self.confidential_dir}")

    def _finish_confidential_recording_timer(self) -> None:
        if self.confidential_started_at is None or self.confidential_ended_at is not None:
            return
        self.confidential_ended_at = datetime.now(timezone.utc)
        self.confidential_duration_seconds = int(
            (self.confidential_ended_at - self.confidential_started_at).total_seconds()
        )
        self.confidential_recording_active = False
        if self.confidential_participants_snapshot_module is not None:
            self.confidential_participants_snapshot_module.stop()
        self._write_confidential_meeting_time()
        self._write_confidential_recording_status("completed")
        self._print(
            "[Bot] Confidential duration: "
            f"{self._format_duration(self.confidential_duration_seconds)}"
        )

    def _write_confidential_meeting_time(self) -> None:
        if self.confidential_started_at is None or self.confidential_dir is None:
            return
        payload = {
            "session_id": self.session_id,
            "title": "\u041a\u043e\u043d\u0444\u0438\u0434\u0435\u043d\u0446\u0438\u0430\u043b\u044c\u043d\u0430\u044f \u0447\u0430\u0441\u0442\u044c",
            "parent_meeting_title": self._meeting_artifacts().title,
            "participants": self.confidential_participants,
            "started_at": self.confidential_started_at.isoformat(),
            "ended_at": self.confidential_ended_at.isoformat() if self.confidential_ended_at else None,
            "duration_seconds": self.confidential_duration_seconds,
            "duration_formatted": self._format_duration(self.confidential_duration_seconds),
            "max_participants": self.confidential_max_participants,
            "audio_path": str(self.confidential_dir / "recording_meeting.wav"),
        }
        (self.confidential_dir / "meeting_time.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _write_confidential_recording_status(self, status: str) -> None:
        if self.confidential_started_at is None or self.confidential_dir is None:
            return
        audio_path = self.confidential_dir / "recording_meeting.wav"
        payload = {
            "session_id": self.session_id,
            "status": status,
            "participants": self.confidential_participants,
            "started_at": self.confidential_started_at.isoformat(),
            "ended_at": self.confidential_ended_at.isoformat() if self.confidential_ended_at else None,
            "duration_seconds": self.confidential_duration_seconds,
            "duration_formatted": self._format_duration(self.confidential_duration_seconds),
            "max_participants": self.confidential_max_participants,
            "audio_path": str(audio_path),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        (self.confidential_dir / "confidential_recording_status.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def get_participant_count(self) -> int:
        """Возвращает количество других участников на встрече (исключая бота)."""
        try:
            result = await self.page.evaluate('''() => {
                // === 1. Ищем число участников в интерфейсе ===
                const patterns = [
                    /^\u0423\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u0438\s*[:.]?\s*(\d+)$/i,
                    /^\u0423\u0447\u0430\u0441\u0442\u043d\u0438\u043a\s*[:.]?\s*(\d+)$/i,
                    /^Participants\s*[:.]?\s*(\d+)$/i,
                    /^Participant\s*[:.]?\s*(\d+)$/i,
                ];
                const isVisible = (element) => {
                    const rect = element.getBoundingClientRect();
                    const style = window.getComputedStyle(element);
                    return rect.width > 0 && rect.height > 0 &&
                        style.display !== 'none' &&
                        style.visibility !== 'hidden' &&
                        Number(style.opacity || '1') !== 0;
                };
                const clean = (value) => (value || '').replace(/\s+/g, ' ').trim();

                let interfaceCount = null;
                const countElements = Array.from(document.querySelectorAll('button, [role="button"], [aria-label], [title], [data-testid]'))
                    .filter(isVisible)
                    .map((el) => clean(`${el.innerText || el.textContent || ''} ${el.getAttribute('aria-label') || ''} ${el.getAttribute('title') || ''}`))
                    .filter((text) => text.length > 0 && text.length <= 40);

                for (const text of countElements) {
                    for (const pattern of patterns) {
                        const match = text.match(pattern);
                        if (match) {
                            interfaceCount = parseInt(match[1], 10);
                            break;
                        }
                    }
                    if (interfaceCount !== null) break;
                }

                // === 2. Проверяем, вошёл ли бот (есть ли его видео) ===
                const hasSelfVideo = !!document.querySelector(
                    '[class*="self"] video, ' +
                    '[class*="local"] video, ' +
                    '[data-is-me="true"] video'
                );

                // === 3. Считаем видео-элементы других участников (fallback) ===
                const videos = document.querySelectorAll('video');
                let videoCount = 0;
                for (const vid of videos) {
                    const parent = vid.closest('[class*="self"], [class*="local"], [data-is-me="true"]');
                    if (!parent) {
                        videoCount++;
                    }
                }

                // === 4. Принимаем решение ===
                let count = 0;
                if (interfaceCount !== null) {
                    // Если нашли число в интерфейсе — вычитаем бота (если он вошёл)
                    count = interfaceCount - 1;
                } else {
                    // Fallback: используем количество видео
                    count = videoCount;
                }

                return Math.max(0, count);
            }''')

            raw_count = int(result)
            max_expected = int(os.getenv("TELEMOST_MAX_EXPECTED_PARTICIPANTS", "15"))
            spike_delta = int(os.getenv("TELEMOST_PARTICIPANT_SPIKE_DELTA", "10"))
            previous = self._last_valid_participant_count

            if raw_count > max_expected:
                self._print(
                    "[Bot] Ignoring participant count above limit: "
                    f"{raw_count} > {max_expected}; using previous valid count {previous}"
                )
                return previous

            if previous > 0 and raw_count > previous and (raw_count - previous) > spike_delta:
                self._print(
                    "[Bot] Ignoring participant count spike: "
                    f"{previous} -> {raw_count}; using previous valid count {previous}"
                )
                return previous

            self._last_valid_participant_count = raw_count
            self._print(f"[Bot] Other participants count: {raw_count}")
            return raw_count

        except Exception as e:
            self._print(f"[Bot] Could not get participant count: {e}")
            return self._last_valid_participant_count

    async def _is_in_meeting_room(self) -> bool:
        if self.page is None or self.page.is_closed():
            return False
        try:
            return bool(await self.page.evaluate('''() => {
                const visible = (node) => {
                    if (!node) return false;
                    const style = window.getComputedStyle(node);
                    const rect = node.getBoundingClientRect();
                    return style.visibility !== 'hidden' &&
                        style.display !== 'none' &&
                        rect.width > 0 &&
                        rect.height > 0;
                };
                const buttons = Array.from(document.querySelectorAll('button'));
                const hasPreJoinButton = buttons.some((button) => {
                    if (!visible(button)) return false;
                    const text = (button.innerText || button.textContent || '').trim();
                    return text.includes('Продолжить в браузере') ||
                        text.includes('Подключиться') ||
                        text.includes('Присоединиться') ||
                        text.includes('Войти');
                });
                if (hasPreJoinButton) return false;

                const bodyText = document.body ? document.body.innerText : '';
                const looksDisconnected =
                    bodyText.includes('Соединение потеряно') ||
                    bodyText.includes('Переподключение') ||
                    bodyText.includes('Повторить') ||
                    bodyText.includes('Connection lost') ||
                    bodyText.includes('Reconnecting');
                if (looksDisconnected) return false;

                return !!document.querySelector('[data-testid="participant-item"]') ||
                    !!document.querySelector('[data-testid="mute-audio"]') ||
                    !!document.querySelector('[data-testid="leave-call"]') ||
                    !!document.querySelector('video') ||
                    !!document.querySelector('[class*="participant"]');
            }'''))
        except Exception as e:
            self._print(f"[Bot] Meeting room state check failed: {e}")
            return False

    async def _try_reconnect(self, meeting_url: str, session_id: str, config: dict, attempt: int) -> bool:
        detected_at = datetime.now(timezone.utc)
        max_attempts = int(config.get("max_reconnect_attempts", 3))
        delay_seconds = int(config.get("reconnect_interval_sec", 10))
        total_limit_seconds = int(config.get("reconnect_total_limit_seconds", 300))
        deadline = asyncio.get_running_loop().time() + total_limit_seconds

        while attempt <= max_attempts and asyncio.get_running_loop().time() <= deadline:
            event = {
                "detected_at": detected_at.isoformat(),
                "attempt": attempt,
                "status": "started",
            }
            self.reconnect_events.append(event)
            self._write_meeting_time()
            self._print(f"[Bot] Reconnect attempt {attempt}/{max_attempts}")

            try:
                if self.page is not None and not self.page.is_closed():
                    await self.page.close()
                self.page = None
                await asyncio.sleep(delay_seconds)
                await self.join(meeting_url, session_id)
                if await self._is_in_meeting_room():
                    reconnected_at = datetime.now(timezone.utc)
                    event["status"] = "success"
                    event["reconnected_at"] = reconnected_at.isoformat()
                    event["downtime_seconds"] = int((reconnected_at - detected_at).total_seconds())
                    self._write_meeting_time()
                    self._print("[Bot] Reconnected to meeting")
                    return True
                event["status"] = "failed"
                event["error"] = "meeting room was not detected after reconnect"
            except Exception as e:
                event["status"] = "failed"
                event["error"] = str(e)
                self._print(f"[Bot] Reconnect attempt failed: {e}")
            self._write_meeting_time()
            attempt += 1

        self._print("[Bot] Reconnect attempts exhausted")
        return False

    async def leave(self):
        """Закрывает браузер и завершает запись."""
        self._finish_meeting_timer()
        self._finish_confidential_recording_timer()
        self._stop_recording()

        if self.participants_snapshot_module is not None:
            self.participants_snapshot_module.stop()
        if self.confidential_participants_snapshot_module is not None:
            self.confidential_participants_snapshot_module.stop()
        if self.agenda_tracker is not None:
            self.agenda_tracker.finish()
        self._write_participants_summary()

        if self.page:
            await self.page.close()
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self._playwright:
            await self._playwright.stop()
        self._print("[Bot] Left meeting")

    def _write_participants_summary(self) -> None:
        if not self.meeting_artifacts:
            return
        try:
            ParticipantsSummaryBuilder(
                self.meeting_artifacts.meeting_dir,
                expected_participants=self.expected_participants_text,
                logger=self._print,
            ).build()
        except Exception as error:
            self._print(f"[Bot] Participants summary failed: {error}")

    async def run(self, meeting_url: str, config: dict):
        """Основной цикл работы бота: вход, мониторинг, выход."""
        session_id = config.get("session_id", None)
        self.meeting_title = config.get("title")
        self.agenda_text = config.get("agenda")
        self.expected_participants_text = config.get("expected_participants")
        await self.join(meeting_url, session_id)

        alone_seconds = 0
        attempt = 0
        lost_checks = 0
        reconnect_enabled = config.get("reconnect_enabled", True)
        max_participants = 0  # начинаем с 0, так как бот считает других участников

        while True:
            if reconnect_enabled:
                in_meeting = await self._is_in_meeting_room()
                lost_checks = 0 if in_meeting else lost_checks + 1
                if lost_checks >= int(config.get("reconnect_lost_checks", 2)):
                    self._print("[Bot] Meeting connection appears lost")
                    reconnected = await self._try_reconnect(
                        meeting_url,
                        session_id,
                        config,
                        attempt + 1,
                    )
                    if not reconnected:
                        self._print("[Bot] Leaving because reconnect failed")
                        break
                    attempt += 1
                    lost_checks = 0
                    continue

            if self.chat_exit_requested:
                self._print("[Bot] Leaving after chat exit command")
                break

            if self.confidential_no_recording_leave_requested:
                self._print("[Bot] Leaving after confidential no-recording command")
                break

            participants = await self.get_participant_count()
            self._print(f"[Bot] Participants: {participants}")
            snapshot_module = self._active_participants_snapshot_module()
            if snapshot_module is not None:
                snapshot_module.observe_participants_count(participants)

            # Сохраняем максимальное количество участников (исключая бота)
            if participants > max_participants:
                max_participants = participants
                self._print(f"[Bot] Max participants detected: {max_participants}")
            if self.confidential_recording_active and participants > self.confidential_max_participants:
                self.confidential_max_participants = participants
                self._write_confidential_meeting_time()
                self._print(f"[Bot] Confidential max participants detected: {self.confidential_max_participants}")

            if participants == 0:
                alone_seconds += 5
                if should_leave(alone_seconds, config.get("alone_leave_threshold", 20)):
                    self._print("[Bot] Leaving due to being alone too long")
                    break
            else:
                alone_seconds = 0

            await asyncio.sleep(5)

        # Сохраняем максимальное количество участников в конфиг для транскрипции
        config["max_participants"] = max_participants
        await self.leave()
        config["meeting_started_at"] = (
            self.meeting_started_at.isoformat() if self.meeting_started_at else None
        )
        config["meeting_ended_at"] = (
            self.meeting_ended_at.isoformat() if self.meeting_ended_at else None
        )
        config["meeting_duration_seconds"] = self.meeting_duration_seconds
        config["meeting_duration_formatted"] = self._format_duration(
            self.meeting_duration_seconds
        )
        config["reconnects"] = self.reconnect_events
        if self.meeting_artifacts:
            config["meeting_dir"] = str(self.meeting_artifacts.meeting_dir)
            config["audio_path"] = str(self.meeting_artifacts.audio_path)
        if self.confidential_dir:
            config["confidential_dir"] = str(self.confidential_dir)
            config["confidential_audio_path"] = str(self.confidential_dir / "recording_meeting.wav")
            config["confidential_max_participants"] = self.confidential_max_participants
