import asyncio
from playwright.async_api import async_playwright
 
async def save_auth_state():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
 
        print("[Auth] Переход на страницу авторизации...")
        await page.goto("https://passport.yandex.ru/auth")
 
        # Ручной вход (вы вводите логин/пароль вручную)
        input("Войдите в аккаунт вручную и нажмите Enter...")
 
        # Переходим на главную Телемоста, чтобы получить cookies для домена
        await page.goto("https://telemost.yandex.ru/")
        await page.wait_for_load_state("networkidle")
 
        # Сохраняем состояние
        await context.storage_state(path="auth_state.json")
        print("[Auth] Состояние сохранено в auth_state.json")
        await browser.close()
 
if __name__ == "__main__":
    asyncio.run(save_auth_state())