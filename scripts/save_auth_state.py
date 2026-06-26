import asyncio
import os
from playwright.async_api import async_playwright

async def save_auth_state():
    LOGIN = 'ai@razum.life'
    PASSWORD = '"nj4H@PEV'

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        print("[Auth] Переход на страницу авторизации...")
        await page.goto("https://passport.yandex.ru/auth")

        # === Авторизация (автоматически) ===
        login_input = await page.query_selector('input[name="login"]')
        if not login_input:
            print("[Auth] Ищем 'Ещё'...")
            try:
                await page.click('text=Ещё', timeout=3000)
            except:
                await page.click('text=Еще', timeout=3000)
            try:
                await page.click('text=Войти по логину', timeout=5000)
            except:
                await page.click('text=Войти с логином', timeout=5000)

        await page.wait_for_selector('input[name="login"], input[name="email"], input[placeholder*="Логин"]', timeout=10000)
        login_field = await page.query_selector('input[name="login"]') or await page.query_selector('input[name="email"]') or await page.query_selector('input[placeholder*="Логин"]')
        if login_field:
            await login_field.fill(LOGIN)
        else:
            print("[Auth] Поле логина не найдено")
            await browser.close()
            return

        await page.click('button:has-text("Войти")', timeout=5000)

        await page.wait_for_selector('input[type="password"], input[name="passwd"]', timeout=15000)
        password_field = await page.query_selector('input[type="password"]') or await page.query_selector('input[name="passwd"]')
        if password_field:
            await password_field.fill(PASSWORD)
        else:
            print("[Auth] Поле пароля не найдено")
            await browser.close()
            return

        await page.click('button:has-text("Далее")', timeout=5000)

        # Ждём перехода на id.yandex.ru
        try:
            await page.wait_for_url("**/id.yandex.ru/**", timeout=15000)
            print("[Auth] Успешный вход в Яндекс ID")
        except:
            print("[Auth] Вход не удался, возможно 2FA")
            await browser.close()
            return

        # === РУЧНОЙ ШАГ: переход в Телемост ===
        print("[Auth] Переход на главную страницу Телемоста...")
        await page.goto("https://telemost.yandex.ru/")

        # Ждём загрузки страницы
        await page.wait_for_load_state("domcontentloaded", timeout=10000)

        # Если появилась страница выбора аккаунта – останавливаемся и просим выбрать вручную
        account_selector = await page.query_selector('button:has-text("@")')
        if account_selector:
            print("=" * 60)
            print("ПОЯВИЛАСЬ СТРАНИЦА ВЫБОРА АККАУНТА.")
            print("Пожалуйста, выберите аккаунт вручную в открывшемся браузере.")
            print("После выбора и полной загрузки страницы нажмите Enter в этом терминале.")
            print("=" * 60)
            input("Нажмите Enter после ручного выбора аккаунта...")

        # Ждём появления какого-либо элемента на главной странице Телемоста
        try:
            await page.wait_for_selector('a[href*="telemost.yandex.ru/new"]', timeout=10000)
            print("[Auth] Главная страница Телемоста загружена")
        except:
            # Если селектор не найден, возможно, интерфейс изменился, просто ждём 5 секунд
            print("[Auth] Не удалось найти элемент, ждём 5 секунд...")
            await asyncio.sleep(5)

        # Сохраняем состояние
        await context.storage_state(path="auth_state.json")
        print("[Auth] Состояние сохранено в auth_state.json (включая Телемост)")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(save_auth_state())