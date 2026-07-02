import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

load_dotenv(ROOT_DIR / ".env")

from core.auth.qr_auth import AUTH_QR_URL
from core.constants import USER_AGENT, VIEWPORT, CHROMIUM_ARGS

DEFAULT_OWNER_REPO = "IVAN-source-spec/telemost-ai-agent-v3"
DEFAULT_STORAGE_STATE_PATH = "data/auth/yandex-session.json"
DEFAULT_COOKIES_PATH = "data/auth/cookies.json"
DEFAULT_ARTIFACT_PATH = "auth_state.artifact.json"
DEFAULT_PROFILE_DIR = ".telemost-browser-profile"
DEFAULT_QR_SCREENSHOT_PATH = "auth_qr.png"
DEFAULT_AFTER_QR_SCREENSHOT_PATH = "auth_after_qr.png"
DEFAULT_QR_READY_TIMEOUT_MS = 60_000
DEFAULT_LOGIN_READY_TIMEOUT_MS = 120_000
AUTH_STAGE_SCREENSHOTS = os.getenv("TELEMOST_AUTH_STAGE_SCREENSHOTS", "1").lower() not in {
    "0",
    "false",
    "no",
}


async def _wait_for_yandex_auth_redirects(page) -> None:
    for _ in range(3):
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
            return
        except PlaywrightError:
            await page.wait_for_timeout(2_000)


async def _open_telemost_home(page) -> None:
    for attempt in range(3):
        try:
            await page.goto(
                "https://telemost.yandex.ru/",
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            try:
                await page.wait_for_load_state("networkidle", timeout=20_000)
            except PlaywrightError:
                pass
            return
        except PlaywrightError as error:
            if "interrupted by another navigation" not in str(error) or attempt == 2:
                raise
            print("[Auth] Yandex is still finishing login redirects; retrying Telemost open...")
            await page.wait_for_timeout(3_000)


def _project_path(path_value: str) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return ROOT_DIR / path


async def _save_screenshot(page, path_value: str, label: str) -> Path:
    screenshot_path = _project_path(path_value)
    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
    await page.screenshot(path=str(screenshot_path), full_page=True)
    print(f"[Auth] Saved {label} screenshot: {screenshot_path}")
    return screenshot_path


async def _save_stage_screenshot(page, stage_name: str, label: str) -> None:
    if not AUTH_STAGE_SCREENSHOTS:
        return
    await _save_screenshot(page, f"auth_stage_{stage_name}.png", label)


async def _save_yandex_cookies(context, cookies_path: Path) -> int:
    cookies = await context.cookies()
    yandex_cookies = [
        cookie
        for cookie in cookies
        if "yandex" in cookie.get("domain", "").lower()
        or cookie.get("domain", "").lower().endswith(".ya.ru")
        or cookie.get("domain", "").lower() == ".ya.ru"
    ]
    cookies_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "cookies": yandex_cookies,
        "saved_at": datetime.utcnow().isoformat(),
        "cookie_count": len(yandex_cookies),
    }
    tmp_path = cookies_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(cookies_path)
    return len(yandex_cookies)


async def _wait_for_qr_code(page, timeout_ms: int) -> None:
    print("[Auth] Waiting for QR code to render...")
    try:
        await page.wait_for_function(
            """() => {
                const isVisible = (node) => {
                    const style = window.getComputedStyle(node);
                    const rect = node.getBoundingClientRect();
                    return style.visibility !== "hidden" &&
                        style.display !== "none" &&
                        rect.width >= 160 &&
                        rect.height >= 160;
                };

                const visualQr = Array.from(document.querySelectorAll("canvas,img,svg"))
                    .find(isVisible);
                if (visualQr) {
                    return true;
                }

                return Array.from(document.querySelectorAll("*")).some((node) => {
                    if (!isVisible(node)) {
                        return false;
                    }
                    const background = window.getComputedStyle(node).backgroundImage || "";
                    return background.includes("data:image") || background.includes("qr");
                });
            }""",
            timeout=timeout_ms,
        )
        await page.wait_for_timeout(1_000)
        print("[Auth] QR code is visible.")
    except PlaywrightError:
        print("[Auth] QR code was not detected in time; saving current page anyway.")


async def _wait_for_qr_login_complete(page, timeout_ms: int) -> None:
    print("[Auth] Waiting for QR login to finish...")
    try:
        await page.wait_for_function(
            """() => {
                const url = window.location.href.toLowerCase();
                const text = document.body ? document.body.innerText : "";
                const hasEmail = /[A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{2,}/i.test(text);
                const hasAccountUi =
                    text.includes("Яндекс ID") ||
                    text.includes("Главная") ||
                    text.includes("Безопасность") ||
                    text.includes("Yandex ID");
                const stillQrPage =
                    text.includes("QR") &&
                    text.includes("Просканируйте") &&
                    !hasEmail;

                return !stillQrPage && (
                    (url.includes("id.yandex") && (hasEmail || hasAccountUi)) ||
                    (url.includes("passport.yandex") && hasEmail) ||
                    url.includes("telemost.yandex")
                );
            }""",
            timeout=timeout_ms,
        )
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightError:
            pass
        print(f"[Auth] QR login finished at: {page.url}")
    except PlaywrightError as error:
        await _save_screenshot(page, "auth_login_timeout.png", "QR login timeout")
        raise RuntimeError(
            "QR login did not finish in time; see auth_login_timeout.png"
        ) from error


async def _is_yandex_account_chooser(page) -> bool:
    url = page.url.lower()
    if not any(host in url for host in ("id.yandex", "passport.yandex", "sso.ya.ru")):
        return False
    try:
        return bool(await page.evaluate(
            """() => {
                const text = document.body ? document.body.innerText : "";
                return /[A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{2,}/i.test(text);
            }"""
        ))
    except PlaywrightError:
        return False


async def _select_yandex_account_if_present(page, account_email: str) -> bool:
    if not await _is_yandex_account_chooser(page):
        return False

    target = await page.evaluate(
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

            const directText = (node) => (node.innerText || node.textContent || "").trim();
            const cardFromNode = (node) => {
                let best = node;
                let current = node;
                for (let depth = 0; current && depth < 8; depth += 1) {
                    if (!isVisible(current)) {
                        current = current.parentElement;
                        continue;
                    }
                    const rect = current.getBoundingClientRect();
                    const text = directText(current);
                    const looksLikeCard =
                        text.includes("@") &&
                        rect.width >= 220 &&
                        rect.width <= 520 &&
                        rect.height >= 48 &&
                        rect.height <= 140 &&
                        !text.includes("Войти в другой аккаунт");
                    if (looksLikeCard) {
                        best = current;
                    }
                    current = current.parentElement;
                }
                return best;
            };
            const targetFromNode = (node, label) => {
                const clickable = node.closest('button,a,[role="button"]') || cardFromNode(node);
                clickable.scrollIntoView({ block: "center", inline: "center" });
                const rect = clickable.getBoundingClientRect();
                return {
                    label,
                    x: rect.left + Math.min(Math.max(rect.width * 0.36, 80), rect.width - 30),
                    y: rect.top + rect.height / 2,
                    centerX: rect.left + rect.width / 2,
                    centerY: rect.top + rect.height / 2,
                    width: rect.width,
                    height: rect.height,
                };
            };
            const nodes = Array.from(document.querySelectorAll('button,a,[role="button"],div,li,span'))
                .filter(isVisible)
                .sort((left, right) => {
                    const leftArea = left.getBoundingClientRect().width * left.getBoundingClientRect().height;
                    const rightArea = right.getBoundingClientRect().width * right.getBoundingClientRect().height;
                    return leftArea - rightArea;
                });

            if (accountEmail) {
                const exact = nodes.find((node) => {
                    const text = node.innerText || node.textContent || "";
                    return text.includes(accountEmail);
                });
                if (exact) {
                    return targetFromNode(exact, `configured account ${accountEmail}`);
                }
            }

            const firstAccount = nodes.find((node) => {
                const text = node.innerText || node.textContent || "";
                return emailRe.test(text);
            });
            if (firstAccount) {
                const text = firstAccount.innerText || firstAccount.textContent || "";
                const match = text.match(emailRe);
                return targetFromNode(firstAccount, `first account ${match ? match[0] : ""}`);
            }
            return null;
        }""",
        account_email,
    )
    if target is None:
        print("[Auth] Account chooser result: account card not found")
        return False

    print(
        "[Auth] Account chooser target: "
        f"{target['label']} at {target['x']:.1f},{target['y']:.1f} "
        f"size {target['width']:.1f}x{target['height']:.1f}"
    )
    await page.mouse.move(float(target["x"]), float(target["y"]))
    await page.mouse.down()
    await page.wait_for_timeout(120)
    await page.mouse.up()
    await page.mouse.click(float(target["x"]), float(target["y"]))
    await page.wait_for_timeout(500)
    await page.mouse.click(float(target["centerX"]), float(target["centerY"]))
    result = f"clicked {target['label']}"
    print(f"[Auth] Account chooser result: {result}")
    print(f"[Auth] Account chooser URL after click: {page.url}")
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=15_000)
    except PlaywrightError:
        pass
    print(f"[Auth] Account chooser URL after load wait: {page.url}")
    await page.wait_for_timeout(3_000)
    return True


async def _resolve_yandex_account_chooser(
    page,
    account_email: str,
    reason: str,
    attempts: int = 5,
) -> bool:
    handled = False
    for attempt in range(attempts):
        await page.wait_for_timeout(1_000)
        if not await _is_yandex_account_chooser(page):
            return handled

        if not await _select_yandex_account_if_present(page, account_email):
            return handled

        handled = True
        print(f"[Auth] Yandex account chooser handled ({reason}), attempt {attempt + 1}/{attempts}")
        for _ in range(10):
            await page.wait_for_timeout(1_000)
            if not await _is_yandex_account_chooser(page):
                print("[Auth] Yandex account chooser resolved.")
                return True

        print("[Auth] Yandex account chooser still present, retrying...")

    if await _is_yandex_account_chooser(page):
        await _save_screenshot(page, "auth_account_chooser_loop.png", "account chooser loop")
        raise RuntimeError(
            "Yandex account chooser loop detected while preparing auth state; "
            "see auth_account_chooser_loop.png"
        )
    return handled


async def _click_visible_control(
    page,
    *,
    labels: list[str],
    test_ids: list[str] | None = None,
    timeout_ms: int = 30_000,
    required: bool = True,
) -> str | None:
    test_ids = test_ids or []
    deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
    while asyncio.get_running_loop().time() < deadline:
        result = await page.evaluate(
            """({labels, testIds}) => {
                const isVisible = (node) => {
                    const style = window.getComputedStyle(node);
                    const rect = node.getBoundingClientRect();
                    return style.visibility !== "hidden" &&
                        style.display !== "none" &&
                        rect.width > 0 &&
                        rect.height > 0;
                };

                const isButtonLike = (node) => {
                    const tag = node.tagName.toLowerCase();
                    return tag === "button" ||
                        tag === "a" ||
                        node.getAttribute("role") === "button";
                };

                const candidates = Array.from(document.querySelectorAll(
                    'button,a,[role="button"],[data-testid]'
                )).filter(isVisible).map((node) => {
                    const text = (node.innerText || node.textContent || "").trim();
                    const testId = node.getAttribute("data-testid") || "";
                    const rect = node.getBoundingClientRect();
                    const testIdMatch = testIds.find((value) => testId === value || testId.includes(value));
                    const labelMatch = labels.find((value) => text.includes(value));
                    const compactEnough = text.length <= 140 && rect.width <= 720 && rect.height <= 220;
                    const usableLabelMatch = !!labelMatch && (isButtonLike(node) || compactEnough);
                    if (!testIdMatch && !usableLabelMatch) {
                        return null;
                    }
                    let rank = 10;
                    if (testIdMatch && isButtonLike(node)) {
                        rank = 0;
                    } else if (labelMatch && isButtonLike(node)) {
                        rank = 1;
                    } else if (testIdMatch) {
                        rank = 2;
                    } else if (labelMatch) {
                        rank = 3;
                    }
                    return {
                        node,
                        text,
                        testId,
                        rank,
                        area: rect.width * rect.height,
                    };
                }).filter(Boolean).sort((left, right) => {
                    if (left.rank !== right.rank) {
                        return left.rank - right.rank;
                    }
                    return left.area - right.area;
                });

                const targetInfo = candidates[0];
                if (!targetInfo) {
                    return null;
                }

                const target = targetInfo.node;

                target.scrollIntoView({ block: "center", inline: "center" });
                const rect = target.getBoundingClientRect();
                target.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true }));
                target.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
                target.click();
                target.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
                target.dispatchEvent(new PointerEvent("pointerup", { bubbles: true }));

                return {
                    text: targetInfo.text,
                    testId: target.getAttribute("data-testid") || "",
                    x: rect.left + rect.width / 2,
                    y: rect.top + rect.height / 2,
                };
            }""",
            {"labels": labels, "testIds": test_ids},
        )
        if result:
            await page.mouse.click(float(result["x"]), float(result["y"]))
            label = (result["text"] or result["testId"] or "").replace("\n", " ")
            if len(label) > 120:
                label = label[:117] + "..."
            print(f"[Auth] Clicked Telemost control: {label}")
            return label
        await page.wait_for_timeout(1_000)

    if required:
        await _save_screenshot(page, "auth_control_not_found.png", "control not found")
        raise RuntimeError(f"Telemost control not found: {labels or test_ids}")
    return None


async def _click_continue_in_browser_like_client(page) -> str:
    try:
        await page.click(
            'xpath=//button[contains(normalize-space(.), "Продолжить в браузере")]',
            timeout=30_000,
        )
        print("[Auth] Clicked 'Продолжить в браузере' via XPath")
        return "clicked via XPath: Продолжить в браузере"
    except Exception as error:
        print(f"[Auth] Continue XPath method failed: {error}")

    result = await page.evaluate(
        """() => {
            const buttons = Array.from(document.querySelectorAll("button"));
            for (const btn of buttons) {
                const text = (btn.innerText || btn.textContent || "").trim();
                if (text.includes("Продолжить")) {
                    btn.scrollIntoView({ behavior: "smooth", block: "center" });
                    btn.click();
                    btn.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
                    btn.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
                    btn.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
                    btn.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true }));
                    btn.dispatchEvent(new PointerEvent("pointerup", { bubbles: true }));
                    return "clicked via JS: " + text;
                }
            }
            return "not found";
        }"""
    )
    print(f"[Auth] 'Продолжить в браузере' result: {result}")
    if result == "not found":
        await _save_screenshot(page, "auth_continue_not_found.png", "continue button not found")
        raise RuntimeError("Could not click 'Продолжить в браузере'")
    return result


async def _click_enter_conference_button_like_client(page, account_email: str = "") -> str:
    if await _is_yandex_account_chooser(page):
        await _resolve_yandex_account_chooser(page, account_email, "before click join")

    try:
        await page.wait_for_selector('[data-testid="enter-conference-button"]', timeout=15_000)
        print("[Auth] Join button found by data-testid")
        await page.wait_for_selector(
            '[data-testid="enter-conference-button"]:not([disabled]):visible',
            timeout=10_000,
        )
        print("[Auth] Join button is visible and enabled")
        result = await page.evaluate(
            """() => {
                const btn = document.querySelector('[data-testid="enter-conference-button"]');
                if (!btn) return "not found";
                if (btn.disabled) return "disabled";
                btn.scrollIntoView({ behavior: "smooth", block: "center" });
                btn.click();
                btn.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
                btn.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
                btn.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
                btn.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true }));
                btn.dispatchEvent(new PointerEvent("pointerup", { bubbles: true }));
                return "clicked";
            }"""
        )
        return result
    except Exception as error:
        print(f"[Auth] Join data-testid method failed: {error}")

    result = await page.evaluate(
        """() => {
            const buttons = Array.from(document.querySelectorAll("button"));
            for (const btn of buttons) {
                const text = (btn.innerText || btn.textContent || "").trim();
                if (text.includes("Подключиться")) {
                    btn.scrollIntoView({ behavior: "smooth", block: "center" });
                    btn.click();
                    btn.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
                    btn.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
                    btn.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
                    btn.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true }));
                    btn.dispatchEvent(new PointerEvent("pointerup", { bubbles: true }));
                    return "clicked via text: " + text;
                }
            }
            return "not found";
        }"""
    )
    print(f"[Auth] 'Подключиться' result: {result}")
    if result == "not found":
        await _save_screenshot(page, "auth_join_not_found.png", "join button not found")
        raise RuntimeError("Could not click 'Подключиться'")
    return result


async def _run_client_join_flow_for_auth(page, meeting_url: str, account_email: str) -> None:
    await page.goto(meeting_url)
    print("[Auth] Navigated to meeting page")
    await _save_stage_screenshot(page, "05_meeting_opened", "meeting opened")
    await _resolve_yandex_account_chooser(page, account_email, "initial navigation")
    await _save_stage_screenshot(page, "06_after_initial_account_check", "after initial account check")

    # === Same transition block as core/browser_bot/client.py: "Продолжить в браузере" ===
    await _save_stage_screenshot(page, "07_before_continue_browser", "before continue in browser")
    try:
        await page.click('xpath=//button[contains(text(), "Продолжить в браузере")]')
        print("[Auth] Clicked 'Продолжить в браузере' via XPath")
        await page.wait_for_selector('button:has-text("Подключиться")', timeout=10_000)
    except Exception as e2:
        print(f"[Auth] XPath method failed: {e2}")
        try:
            result = await page.evaluate('''() => {
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
            print(f"[Auth] 'Продолжить в браузере' result: {result}")
            await page.wait_for_selector('button:has-text("Подключиться")', timeout=10_000)
        except Exception as e3:
            print(f"[Auth] All methods failed: {e3}")

    await page.wait_for_timeout(2_000)
    await _save_stage_screenshot(page, "08_after_continue_browser", "after continue in browser")
    await _resolve_yandex_account_chooser(page, account_email, "after continue in browser")
    await _save_stage_screenshot(page, "09_after_continue_account_check", "after continue account check")

    # === Same transition block as core/browser_bot/client.py: "Подключиться" ===
    await _save_stage_screenshot(page, "10_before_join_click", "before join click")
    try:
        result = await _click_enter_conference_button_like_client(page, account_email)
        print(f"[Auth] JS click result: {result}")
        await page.wait_for_timeout(2_000)
        await _resolve_yandex_account_chooser(page, account_email, "after enter click")

        try:
            await page.wait_for_function(
                '''() => {
                    const url = window.location.href;
                    const accountChooser =
                        url.includes('id.yandex') ||
                        url.includes('passport.yandex') ||
                        url.includes('sso.ya.ru');
                    const meetingUi =
                        !!document.querySelector('[data-testid="participant-item"]') ||
                        !!document.querySelector('video') ||
                        !!document.querySelector('[class*="participant"]');
                    return !accountChooser && (
                        meetingUi ||
                        (url.includes('telemost.yandex') && url.includes('/j/'))
                    );
                }''',
                timeout=15_000,
            )
            print("[Auth] URL changed, meeting joined!")
        except Exception:
            print("[Auth] URL did not change, checking page content...")
            if await _resolve_yandex_account_chooser(page, account_email, "after join timeout"):
                retry_result = await _click_enter_conference_button_like_client(page, account_email)
                print(f"[Auth] Retry JS click result: {retry_result}")
                await page.wait_for_timeout(2_000)
                await _resolve_yandex_account_chooser(page, account_email, "after retry enter click")

            await page.screenshot(path="after_click_join.png")
            print("[Auth] Screenshot saved as after_click_join.png")

            content = await page.evaluate('''() => {
                return {
                    text: document.body.innerText,
                    hasVideo: !!document.querySelector('video'),
                    hasParticipants: !!document.querySelector('[data-testid="participant-item"]')
                };
            }''')
            print(f"[Auth] Page content: {content}")

    except Exception as e:
        print(f"[Auth] Could not click 'Подключиться': {e}")
        await page.screenshot(path="join_error.png")

    await page.wait_for_timeout(2_000)
    await _save_stage_screenshot(page, "11_after_join_click", "after join click")

    try:
        await page.wait_for_selector(
            '[data-testid="participant-item"], video, [class*="participant"]',
            timeout=5_000,
        )
        print("[Auth] Meeting page detected")
        await _save_stage_screenshot(page, "13_meeting_ui_reached", "meeting UI reached")
    except Exception:
        if await _is_yandex_account_chooser(page):
            await page.screenshot(path="yandex_account_loop.png")
            raise RuntimeError(
                "Meeting was not joined because Yandex account chooser is still open"
            )
        print("[Auth] Meeting page not detected, but continuing")
        await _save_screenshot(page, "auth_meeting_warmup_state.png", "meeting warmup state")
        await _save_stage_screenshot(page, "13_meeting_warmup_unconfirmed", "meeting warmup unconfirmed")

    title = await page.title()
    print(f"[Auth] Page title: {title}")
    html = await page.content()
    print(f"[Auth] HTML length: {len(html)}")


async def _warm_up_telemost_meeting_sso(
    page,
    meeting_url: str,
    account_email: str,
) -> None:
    print(f"[Auth] Warming up Telemost meeting SSO deeply: {meeting_url}")
    await _save_stage_screenshot(page, "04_before_meeting_open", "before meeting open")
    await _run_client_join_flow_for_auth(page, meeting_url, account_email)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Authorize in Yandex Passport, save Playwright storage_state, "
            "and sign a portable session artifact."
        )
    )
    parser.add_argument(
        "--session-id",
        default=os.getenv("TELEMOST_SESSION_ID", "telemost-bot"),
        help="Stable id for this authorized browser session.",
    )
    parser.add_argument(
        "--owner-repo",
        default=os.getenv("TELEMOST_OWNER_REPO", DEFAULT_OWNER_REPO),
        help="Repository scope for the signed session artifact, in owner/repo form.",
    )
    parser.add_argument(
        "--storage-state",
        default=os.getenv("TELEMOST_AUTH_STATE_PATH", DEFAULT_STORAGE_STATE_PATH),
        help="Where to save Playwright storage_state JSON, AIProjects-style.",
    )
    parser.add_argument(
        "--cookies-path",
        default=os.getenv("TELEMOST_AUTH_COOKIES_PATH", DEFAULT_COOKIES_PATH),
        help="Where to save Yandex cookies fallback JSON, AIProjects-style.",
    )
    parser.add_argument(
        "--artifact-path",
        default=os.getenv("TELEMOST_SESSION_ARTIFACT_PATH", DEFAULT_ARTIFACT_PATH),
        help="Deprecated compatibility option. AIProjects-style auth does not require an artifact.",
    )
    parser.add_argument(
        "--ttl-seconds",
        type=int,
        default=int(os.getenv("TELEMOST_SESSION_TTL_SECONDS", "2592000")),
        help="How long the signed artifact is valid.",
    )
    parser.add_argument(
        "--account-email",
        default=os.getenv("TELEMOST_YANDEX_ACCOUNT", ""),
        help="Yandex account email to choose when the account chooser is shown.",
    )
    parser.add_argument(
        "--meeting-url",
        default=os.getenv("TELEMOST_AUTH_MEETING_URL", ""),
        help="Deprecated compatibility option. AIProjects-style auth saves state right after QR login.",
    )
    parser.add_argument(
        "--profile-dir",
        default=os.getenv("TELEMOST_BROWSER_PROFILE_DIR", ""),
        help=(
            "Deprecated compatibility option. AIProjects-style auth uses storage_state, not a persistent profile."
        ),
    )
    parser.add_argument(
        "--qr-debug-only",
        action="store_true",
        default=os.getenv("TELEMOST_QR_DEBUG_ONLY", "").lower() in {"1", "true", "yes"},
        help=(
            "Open Yandex QR auth, save screenshots before and after phone scan, "
            "then exit without saving auth artifacts. Intended for VM diagnostics."
        ),
    )
    parser.add_argument(
        "--qr-screenshot",
        default=os.getenv("TELEMOST_AUTH_QR_SCREENSHOT_PATH", DEFAULT_QR_SCREENSHOT_PATH),
        help="Screenshot path for the initial Yandex QR page. Relative paths are saved in project root.",
    )
    parser.add_argument(
        "--after-qr-screenshot",
        default=os.getenv(
            "TELEMOST_AUTH_AFTER_QR_SCREENSHOT_PATH",
            DEFAULT_AFTER_QR_SCREENSHOT_PATH,
        ),
        help=(
            "Screenshot path after the QR code is scanned and Enter is pressed. "
            "Relative paths are saved in project root."
        ),
    )
    parser.add_argument(
        "--qr-ready-timeout-ms",
        type=int,
        default=int(os.getenv("TELEMOST_AUTH_QR_READY_TIMEOUT_MS", str(DEFAULT_QR_READY_TIMEOUT_MS))),
        help="How long to wait for the QR code image before saving the first screenshot.",
    )
    parser.add_argument(
        "--login-ready-timeout-ms",
        type=int,
        default=int(
            os.getenv(
                "TELEMOST_AUTH_LOGIN_READY_TIMEOUT_MS",
                str(DEFAULT_LOGIN_READY_TIMEOUT_MS),
            )
        ),
        help="How long to wait for Yandex to finish QR login after Enter is pressed.",
    )
    return parser


async def save_auth_state(args: argparse.Namespace) -> None:
    storage_state_path = _project_path(args.storage_state)
    cookies_path = _project_path(args.cookies_path)

    async with async_playwright() as p:
        if args.profile_dir:
            print(
                "[Auth] --profile-dir is ignored: AIProjects-style auth uses "
                "browser.new_context(storage_state=...), not a persistent profile."
            )
        browser = await p.chromium.launch(
            headless=False,
            args=CHROMIUM_ARGS,
        )
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport=VIEWPORT,
        )
        page = await context.new_page()

        print(f"[Auth] Open Yandex QR auth: {AUTH_QR_URL}")
        await page.goto(AUTH_QR_URL, wait_until="domcontentloaded", timeout=60_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightError:
            pass
        await _wait_for_qr_code(page, args.qr_ready_timeout_ms)
        await _save_screenshot(page, args.qr_screenshot, "QR auth")
        await _save_stage_screenshot(page, "01_qr_ready", "QR ready")

        input("[Auth] Scan QR on your phone, wait until phone confirms login, then press Enter...")
        await _wait_for_qr_login_complete(page, args.login_ready_timeout_ms)
        await _save_screenshot(page, args.after_qr_screenshot, "after QR login")
        await _save_stage_screenshot(page, "02_after_qr_login", "after QR login")

        if args.qr_debug_only:
            print("[Auth] QR debug mode finished without saving auth_state/artifact.")
            await context.close()
            await browser.close()
            return

        await _wait_for_yandex_auth_redirects(page)

        storage_state_path.parent.mkdir(parents=True, exist_ok=True)
        await _save_stage_screenshot(page, "03_before_storage_state_save", "before storage state save")
        await context.storage_state(path=str(storage_state_path))
        if not storage_state_path.exists() or storage_state_path.stat().st_size == 0:
            raise RuntimeError(f"Storage state was not written: {storage_state_path}")
        cookie_count = await _save_yandex_cookies(context, cookies_path)
        await _save_stage_screenshot(page, "04_after_storage_state_save", "after storage state save")

        await context.close()
        await browser.close()

    print(f"[Auth] Saved browser storage state: {storage_state_path}")
    print(f"[Auth] Saved Yandex cookies fallback: {cookies_path} ({cookie_count} cookies)")


def main() -> int:
    args = build_parser().parse_args()
    asyncio.run(save_auth_state(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
