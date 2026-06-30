import asyncio
import os
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright
from core.meeting_runtime.participant_policy import should_leave
from core.browser_bot.connection_monitor import plan_reconnect
from core.recording.audio_recorder import AudioRecorder
from core.auth.session_portability import resolve_verified_storage_state_path
from pathlib import Path
from datetime import datetime

DEFAULT_STORAGE_STATE_PATH = "auth_state.json"
DEFAULT_ARTIFACT_PATH = "auth_state.artifact.json"
MAX_ACCOUNT_CHOOSER_ATTEMPTS = 3


class TelemostBot:
    def __init__(
        self,
        headless: bool = False,
        auth_state_path: str | Path | None = None,
        auth_artifact_path: str | Path | None = None,
    ):
        self.headless = headless
        self.page = None
        self.browser = None
        self.context = None
        self._playwright = None
        self.recorder = None
        self.session_id = None
        self.auth_ok = False
        self.auth_state_path = Path(
            auth_state_path
            or os.getenv("TELEMOST_AUTH_STATE_PATH", DEFAULT_STORAGE_STATE_PATH)
        )
        self.auth_artifact_path = Path(
            auth_artifact_path
            or os.getenv("TELEMOST_SESSION_ARTIFACT_PATH", DEFAULT_ARTIFACT_PATH)
        )

    async def _is_yandex_account_chooser(self) -> bool:
        url = (self.page.url if self.page else "").lower()
        if not any(host in url for host in ("id.yandex", "passport.yandex", "sso.ya.ru")):
            return False
        try:
            return bool(await self.page.evaluate(
                """() => {
                    const text = document.body ? document.body.innerText : "";
                    return /[A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{2,}/i.test(text);
                }"""
            ))
        except Exception:
            return False

    async def _select_yandex_account(self) -> str:
        account_email = os.getenv("TELEMOST_YANDEX_ACCOUNT", "").strip()
        
        result = await self.page.evaluate(
            """(accountEmail) => {
                const emailRe = /[A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{2,}/i;
                const isVisible = (node) => {
                    const style = window.getComputedStyle(node);
                    const rect = node.getBoundingClientRect();
                    return style.visibility !== "hidden" &&
                        style.display !== "none" &&
                        rect.width > 0 &&
                        rect.height > 0;
                };
                const clickNode = (node) => {
                    const clickable = node.closest('button,a,[role="button"],[tabindex]') || node;
                    clickable.click();
                };
                const nodes = Array.from(document.querySelectorAll(
                    'button,a,[role="button"],[tabindex],div,li'
                )).filter(isVisible);
    
                if (accountEmail) {
                    const exact = nodes.find((node) => {
                        const text = node.innerText || node.textContent || "";
                        return text.includes(accountEmail);
                    });
                    if (exact) {
                        clickNode(exact);
                        return `clicked configured account ${accountEmail}`;
                    }
                }
    
                const firstAccount = nodes.find((node) => {
                    const text = node.innerText || node.textContent || "";
                    return emailRe.test(text);
                });
                if (firstAccount) {
                    const text = firstAccount.innerText || firstAccount.textContent || "";
                    const match = text.match(emailRe);
                    clickNode(firstAccount);
                    return `clicked first account ${match ? match[0] : ""}`;
                }
                return "account card not found";
            }""",
            account_email,
        )
    
        # === ДОБАВЛЯЕМ: Проверяем, что выбор сработал ===
        # Ждём, пока страница выбора аккаунта исчезнет или появится кнопка "Подключиться"
        for _ in range(10):  # ждём до 10 секунд
            await self.page.wait_for_timeout(1000)
            if not await self._is_yandex_account_chooser():
                print("[Bot] Account chooser page closed successfully")
                break
            # Если страница выбора всё ещё здесь, пробуем кликнуть по любому аккаунту ещё раз
            try:
                await self.page.click('button:has-text("ai@razum.life")', timeout=1000)
                print("[Bot] Retried clicking account")
            except:
                pass
    
        return result

    async def _resolve_yandex_account_chooser(self, meeting_url: str, reason: str) -> bool:
        handled = False
        for attempt in range(MAX_ACCOUNT_CHOOSER_ATTEMPTS):
            await self.page.wait_for_timeout(1000)
            if not await self._is_yandex_account_chooser():
                return handled
    
            handled = True
            print(
                "[Bot] Yandex account chooser detected "
                f"({reason}), attempt {attempt + 1}/{MAX_ACCOUNT_CHOOSER_ATTEMPTS}"
            )
            result = await self._select_yandex_account()
            print(f"[Bot] Account chooser result: {result}")
    
            # === ДОБАВЛЯЕМ: Ждём, пока страница изменится ===
            for _ in range(5):  # до 5 секунд
                await self.page.wait_for_timeout(1000)
                if not await self._is_yandex_account_chooser():
                    print("[Bot] Account chooser resolved")
                    return True
    
            print("[Bot] Account chooser still present, retrying...")
    
        if await self._is_yandex_account_chooser():
            await self.page.screenshot(path="yandex_account_loop.png")
            raise RuntimeError(
                "Yandex account chooser loop detected; "
                "authorized browser state is not accepted by Telemost"
            )
        return handled

    async def _click_enter_conference_button(self) -> str:
        # === ДОБАВЛЯЕМ: Если перед кликом появилась страница выбора аккаунта — обрабатываем ===
        if await self._is_yandex_account_chooser():
            await self._resolve_yandex_account_chooser(self.page.url, "before click join")
    
        await self.page.wait_for_selector('[data-testid="enter-conference-button"]', timeout=15000)
        print("[Bot] Join button found")
        await self.page.wait_for_selector(
            '[data-testid="enter-conference-button"]:not([disabled]):visible',
            timeout=10000,
        )
        print("[Bot] Join button is visible and enabled")
        return await self.page.evaluate('''() => {
            const btn = document.querySelector('[data-testid="enter-conference-button"]');
            if (!btn) return 'not found';
            if (btn.disabled) return 'disabled';
            btn.scrollIntoView({ behavior: 'smooth', block: 'center' });
            btn.click();
            btn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
            btn.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
            btn.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
            btn.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
            btn.dispatchEvent(new PointerEvent('pointerup', { bubbles: true }));
            return 'clicked';
        }''')


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
        self.browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--use-fake-ui-for-media-stream",
                "--use-fake-device-for-media-stream",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
                "--disable-blink-features=AutomationControlled",
            ]
        )
        context_options = {
            "permissions": ["camera", "microphone"],
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        }
        try:
            storage_state_path = resolve_verified_storage_state_path(
                storage_state_path=self.auth_state_path,
                artifact_path=self.auth_artifact_path,
                secret_key=os.getenv("TELEMOST_SESSION_SECRET_KEY"),
            )
        except ValueError as error:
            storage_state_path = None
            print(f"[Bot] Authorized session rejected: {error}")

        if storage_state_path is not None:
            context_options["storage_state"] = str(storage_state_path)
            self.auth_ok = True
            print(f"[Bot] Loaded authorized session: {storage_state_path}")
        else:
            self.auth_ok = False
            print("[Bot] Authorized session unavailable or invalid; using guest mode")

        self.context = await self.browser.new_context(**context_options)
        self.page = await self.context.new_page()
        await self.context.grant_permissions(["camera", "microphone"], origin=meeting_url)
    
        await self.page.goto(meeting_url)
        print("[Bot] Navigated to meeting page")
        await self._resolve_yandex_account_chooser(meeting_url, "initial navigation")



                # === Обработка кнопки "Продолжить в браузере" ===

        try:
            # Способ 2: по точному тексту через XPath
            await self.page.click('xpath=//button[contains(text(), "Продолжить в браузере")]')
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

        await self._resolve_yandex_account_chooser(meeting_url, "after continue in browser")


        # === НАЖИМАЕМ "ПОДКЛЮЧИТЬСЯ" (радикальное решение) ===
        try:
            result = await self._click_enter_conference_button()
            print(f"[Bot] JS click result: {result}")
            await self.page.wait_for_timeout(2000)
            await self._resolve_yandex_account_chooser(meeting_url, "after enter click")
            
            # Ждём изменения URL (признак входа)
            try:
                await self.page.wait_for_function(
                    '''() => {
                        const url = window.location.href;
                        return url.includes('/j/') && url !== 'https://telemost.yandex.ru/j/37383287310143?from_passport=1';
                    }''',
                    timeout=15000
                )
                print("[Bot] URL changed, meeting joined!")
            except:
                print("[Bot] URL did not change, checking page content...")
                if await self._resolve_yandex_account_chooser(meeting_url, "after join timeout"):
                    retry_result = await self._click_enter_conference_button()
                    print(f"[Bot] Retry JS click result: {retry_result}")
                    await self.page.wait_for_timeout(2000)
                    await self._resolve_yandex_account_chooser(meeting_url, "after retry enter click")
                
                # Проверяем, не появилось ли окно выбора аккаунта
                await self.page.screenshot(path="after_click_join.png")
                print("[Bot] Screenshot saved as after_click_join.png")
                
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
            await self.page.screenshot(path="join_error.png")


    
        # Даём время на загрузку интерфейса встречи
        # await asyncio.sleep(3)
    
        try:
            await self.page.wait_for_selector('[data-testid="participant-item"], video, [class*="participant"]', timeout=5000)
            print("[Bot] Meeting page detected")
        except:
            if await self._is_yandex_account_chooser():
                await self.page.screenshot(path="yandex_account_loop.png")
                raise RuntimeError(
                    "Meeting was not joined because Yandex account chooser is still open"
                )
            print("[Bot] Meeting page not detected, but continuing")

        await self._mute_microphone_js()

        self._start_recording()


        await asyncio.sleep(10)

        #=====СКРИНШОТ=====
        await self.page.screenshot(path="debug_screenshot_2.png")
        title = await self.page.title()
        print(f"[Bot] Page title: {title}")
        html = await self.page.content()
        print(f"[Bot] HTML length: {len(html)}")
        #=====СКРИНШОТ=====


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
        self._stop_recording()

        if self.page:
            await self.page.close()
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
