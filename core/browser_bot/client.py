import asyncio
import json
import os
from urllib.parse import urlparse
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright
from core.meeting_runtime.participant_policy import should_leave
from core.browser_bot.connection_monitor import plan_reconnect
from core.recording.audio_recorder import AudioRecorder
from pathlib import Path
from datetime import datetime, timezone
from core.constants import USER_AGENT, VIEWPORT, CHROMIUM_ARGS

DEFAULT_STORAGE_STATE_PATH = "data/auth/yandex-session.json"
DEFAULT_COOKIES_PATH = "data/auth/cookies.json"
DEFAULT_ARTIFACT_PATH = "auth_state.artifact.json"
DEFAULT_PROFILE_DIR = ".telemost-browser-profile"
COMPOSITOR_SCRIPT_PATH = Path(__file__).resolve().parent / "assets" / "compositor.js"


class TelemostBot:
    def __init__(
            self,
            headless: bool = False,
            auth_state_path: str | Path | None = None,
            auth_artifact_path: str | Path | None = None,
            profile_dir: str | Path | None = None,
    ):
        self.headless = headless
        self.page = None
        self.browser = None
        self.context = None
        self._playwright = None
        self.recorder = None
        self.session_id = None
        self.meeting_started_at = None
        self.meeting_ended_at = None
        self.meeting_duration_seconds = 0
        self.auth_ok = False
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
            print(f"[Bot] Loaded Yandex cookies fallback: {self.auth_cookies_path} ({len(cookies)} cookies)")
            return True
        except Exception as error:
            print(f"[Bot] Failed to load Yandex cookies fallback: {error}")
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
                    print(f"[Bot] Entered guest name: {self.display_name}")
                    await self.page.wait_for_timeout(1000)
                    return
            except Exception:
                continue
        print("[Bot] Guest name input not found or not needed")

    async def _click_continue_button_legacy(self, meeting_url: str) -> None:
        try:
            # Способ 2: по точному тексту через XPath
            await self.page.click('xpath=//button[contains(text(), "Продолжить в браузере")]', timeout=30000)
            print("[Bot] Clicked 'Продолжить в браузере' via XPath")
            await self.page.wait_for_selector('button:has-text("Подключиться")', timeout=10000)
        except Exception as e2:
            print(f"[Bot] XPath method failed: {e2}")
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
                print(f"[Bot] 'Продолжить в браузере' result: {result}")
                await self.page.wait_for_selector('button:has-text("Подключиться")', timeout=10000)
            except Exception as e3:
                print(f"[Bot] All methods failed: {e3}")

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
            print(f"[Bot] 'Продолжить в браузере' result: {result}")
            if result == "not found":
                raise RuntimeError("Continue button not found via JS")
            await self.page.wait_for_selector('button:has-text("Подключиться")', timeout=10000)
            return
        except Exception as e1:
            print(f"[Bot] JS method failed: {e1}")

        try:
            await self.page.click(
                'xpath=//button[contains(text(), "Продолжить в браузере")]',
                timeout=5000,
            )
            print("[Bot] Clicked 'Продолжить в браузере' via XPath")
            await self.page.wait_for_selector('button:has-text("Подключиться")', timeout=10000)
        except Exception as e2:
            print(f"[Bot] All methods failed: {e2}")

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
            print(f"[Bot] Microphone toggle result: {result}")
        except Exception as e:
            print(f"[Bot] Mute error: {e}")

    async def join(self, meeting_url: str, session_id: str = None):
        self.session_id = session_id

        self._playwright = await async_playwright().start()
        context_options = {
            "permissions": ["camera", "microphone"],
            "user_agent": USER_AGENT,
            "viewport": VIEWPORT,
        }

        if self.profile_dir is not None:
            print(
                "[Bot] TELEMOST_BROWSER_PROFILE_DIR is ignored: "
                "AIProjects-style auth uses storage_state, not a persistent profile"
            )

        self.browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=CHROMIUM_ARGS,
        )

        storage_state_path = None if self.join_as_guest else self._resolve_storage_state_path()
        if self.join_as_guest:
            self.auth_ok = False
            print("[Bot] AIProjects guest mode enabled: Yandex storage_state is not loaded for meeting join")
        elif storage_state_path is not None:
            context_options["storage_state"] = str(storage_state_path)
            self.auth_ok = True
            print(f"[Bot] Loaded AIProjects-style storage_state: {storage_state_path}")
        else:
            self.auth_ok = False
            print(f"[Bot] Storage state not found: {self.auth_state_path}")

        self.context = await self.browser.new_context(**context_options)

        if not self.join_as_guest and not self.auth_ok:
            self.auth_ok = await self._load_yandex_cookies()

        if not self.join_as_guest and not self.auth_ok:
            message = "Authorized Yandex session unavailable"
            if self.require_auth:
                raise RuntimeError(message)
            print(f"[Bot] {message}; using guest mode")
        await self._install_timer_camera()
        self.page = await self.context.new_page()
        parsed_meeting_url = urlparse(meeting_url)
        meeting_origin = f"{parsed_meeting_url.scheme}://{parsed_meeting_url.netloc}"
        await self.context.grant_permissions(["camera", "microphone"], origin=meeting_origin)

        await self.page.goto(meeting_url, wait_until="domcontentloaded", timeout=60000)
        print("[Bot] Navigated to meeting page")
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
            print("[Bot] Joining as authenticated user")
        else:
            await self._enter_guest_name()

        # === НАЖИМАЕМ "ПОДКЛЮЧИТЬСЯ" (радикальное решение) ===
        try:
            result = await self._click_ai_projects_join_button()
            print(f"[Bot] JS click result: {result}")
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
                print("[Bot] URL changed, meeting joined!")
            except:
                print("[Bot] URL did not change, checking page content...")

                # Проверяем, не появилось ли что-то новое на странице
                content = await self.page.evaluate('''() => {
                    return {
                        text: document.body.innerText,
                        hasVideo: !!document.querySelector('video'),
                        hasParticipants: !!document.querySelector('[data-testid="participant-item"]')
                    };
                }''')
                print(f"[Bot] Page content: {content}")

        except Exception as e:
            print(f"[Bot] Could not click 'Подключиться': {e}")

        # Даём время на загрузку интерфейса встречи
        # await asyncio.sleep(3)

        try:
            await self.page.wait_for_selector('[data-testid="participant-item"], video, [class*="participant"]',
                                              timeout=5000)
            print("[Bot] Meeting page detected")
        except:
            print("[Bot] Meeting page not detected, but continuing")

        self._start_meeting_timer()
        await self._push_timer_camera_state()
        await self._mute_microphone_js()

        self._start_recording()



    def _meeting_dir(self) -> Path:
        if not self.session_id:
            raise RuntimeError("session_id is required to save meeting artifacts")
        meeting_dir = Path.cwd() / "recordings" / self.session_id
        meeting_dir.mkdir(parents=True, exist_ok=True)
        return meeting_dir

    async def _install_timer_camera(self) -> None:
        if not COMPOSITOR_SCRIPT_PATH.exists():
            raise FileNotFoundError(f"Compositor script not found: {COMPOSITOR_SCRIPT_PATH}")
        script = COMPOSITOR_SCRIPT_PATH.read_text(encoding="utf-8")
        await self.context.add_init_script(script)
        print("[Bot] Timer camera compositor installed")

    async def _push_timer_camera_state(self) -> None:
        if not self.page or not self.meeting_started_at:
            return

        start_time_ms = int(self.meeting_started_at.timestamp() * 1000)
        payload = {
            "scene": "timer",
            "meetingTitle": "Telemost Bot",
            "startTimeMs": start_time_ms,
        }

        try:
            has_compositor = await self.page.evaluate("typeof window.__COMPOSITOR__ !== 'undefined'")
            if not has_compositor:
                script = COMPOSITOR_SCRIPT_PATH.read_text(encoding="utf-8")
                await self.page.evaluate(script)
                print("[Bot] Timer camera compositor reinjected")
            await self.page.evaluate(
                "(data) => window.__COMPOSITOR__ && window.__COMPOSITOR__.updateScene(data)",
                payload,
            )
            print("[Bot] Timer camera state updated")
        except Exception as e:
            print(f"[Bot] Timer camera update failed: {e}")

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
        print(f"[Bot] Meeting timer started at {self.meeting_started_at.isoformat()}")

    def _finish_meeting_timer(self) -> None:
        if self.meeting_started_at is None:
            return
        self.meeting_ended_at = datetime.now(timezone.utc)
        self.meeting_duration_seconds = int(
            (self.meeting_ended_at - self.meeting_started_at).total_seconds()
        )
        self._write_meeting_time()
        print(
            "[Bot] Meeting duration: "
            f"{self._format_duration(self.meeting_duration_seconds)}"
        )

    def _write_meeting_time(self) -> None:
        if self.meeting_started_at is None:
            return
        payload = {
            "session_id": self.session_id,
            "started_at": self.meeting_started_at.isoformat(),
            "ended_at": self.meeting_ended_at.isoformat() if self.meeting_ended_at else None,
            "duration_seconds": self.meeting_duration_seconds,
            "duration_formatted": self._format_duration(self.meeting_duration_seconds),
        }
        path = self._meeting_dir() / "meeting_time.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _start_recording(self):
        """Запускает запись аудио в фоновом потоке."""
        if self.recorder is None:
            self.recorder = AudioRecorder()
            self.recorder.start()
            print("[Bot] Audio recording started")

    def _stop_recording(self):
        if self.recorder is None:
            return
        self.recorder.stop()
        if self.session_id:
            # Создаём папку для встречи
            meeting_dir = Path.cwd() / "recordings" / self.session_id
            meeting_dir.mkdir(parents=True, exist_ok=True)
            filename = meeting_dir / f"recording_{self.session_id}.wav"
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"recording_{timestamp}.wav"
        self.recorder.save(str(filename))
        self.recorder.close()
        self.recorder = None
        print(f"[Bot] Audio recording saved to {filename}")

    async def get_participant_count(self) -> int:
        """Возвращает количество других участников на встрече (исключая бота)."""
        try:
            count = await self.page.evaluate('''() => {    
                // 3. Если ничего не нашли, считаем видео-элементы
                const videos = document.querySelectorAll('video');
                let count = 0;
                for (let vid of videos) {
                    // Исключаем видео бота (обычно оно имеет класс local или находится в элементе с class="self")
                    const parent = vid.closest('[class*="self"], [class*="local"]');
                    if (!parent) {
                        count++;
                    }
                }
                return count;
            }''')
            print(f"[Bot] Other participants count: {count}")
            return count
        except Exception as e:
            print(f"[Bot] Could not get participant count: {e}")
            return 0  # Возвращаем 0, чтобы бот начал отсчёт одиночества

    async def leave(self):
        """Закрывает браузер и завершает запись."""
        self._finish_meeting_timer()
        self._stop_recording()

        if self.page:
            await self.page.close()
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self._playwright:
            await self._playwright.stop()
        print("[Bot] Left meeting")

    async def run(self, meeting_url: str, config: dict):
        """Основной цикл работы бота: вход, мониторинг, выход."""
        session_id = config.get("session_id", None)
        await self.join(meeting_url, session_id)

        alone_seconds = 0
        attempt = 0
        max_participants = 0  # начинаем с 0, так как бот считает других участников

        while True:
            participants = await self.get_participant_count()
            print(f"[Bot] Participants: {participants}")

            # Сохраняем максимальное количество участников (исключая бота)
            if participants > max_participants:
                max_participants = participants
                print(f"[Bot] Max participants detected: {max_participants}")

            if participants == 0:
                alone_seconds += 5
                if should_leave(alone_seconds, config.get("alone_leave_threshold", 20)):
                    print("[Bot] Leaving due to being alone too long")
                    break
            else:
                alone_seconds = 0

            if self.page.is_closed():
                decision = plan_reconnect(
                    previous_participants=participants,
                    attempt=attempt,
                    max_attempts=config.get("max_reconnect_attempts", 3),
                    interval_sec=config.get("reconnect_interval_sec", 10),
                )
                if decision["action"] == "reconnect":
                    print(f"[Bot] Reconnecting after {decision['delay_sec']}s")
                    await asyncio.sleep(decision["delay_sec"])
                    await self.join(meeting_url, session_id)
                    attempt += 1
                    continue
                else:
                    print(f"[Bot] Giving up: {decision['reason']}")
                    break
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
