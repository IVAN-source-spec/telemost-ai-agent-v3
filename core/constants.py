import os

USER_AGENT = os.getenv("TELEMOST_BROWSER_USER_AGENT") or (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

VIEWPORT = {
    "width": int(os.getenv("TELEMOST_VIEWPORT_WIDTH", "1280")),
    "height": int(os.getenv("TELEMOST_VIEWPORT_HEIGHT", "720")),
}

# Аргументы запуска Chromium
CHROMIUM_ARGS = [
    "--use-fake-ui-for-media-stream",
    "--use-fake-device-for-media-stream",
]
