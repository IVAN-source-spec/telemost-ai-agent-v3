// Synthetic camera compositor for Telemost.
// It replaces camera video with a canvas timer stream.
(function () {
    "use strict";

    if (window.__COMPOSITOR__) {
        return;
    }

    window.__COMPOSITOR_VERSION__ = 1;

    const CANVAS_WIDTH = 640;
    const CANVAS_HEIGHT = 480;
    const canvas = document.createElement("canvas");
    canvas.width = CANVAS_WIDTH;
    canvas.height = CANVAS_HEIGHT;
    const ctx = canvas.getContext("2d");

    let sceneData = {
        scene: "timer",
        meetingTitle: "Telemost Bot",
        startTimeMs: Date.now(),
    };
    let renderLoopStarted = false;

    function pad(value) {
        return String(value).padStart(2, "0");
    }

    function formatTime(totalSeconds) {
        const safeSeconds = Math.max(0, Math.floor(totalSeconds));
        const hours = Math.floor(safeSeconds / 3600);
        const minutes = Math.floor((safeSeconds % 3600) / 60);
        const seconds = safeSeconds % 60;
        return `${pad(hours)}:${pad(minutes)}:${pad(seconds)}`;
    }

    function truncateText(text, maxWidth, font) {
        ctx.font = font;
        if (ctx.measureText(text).width <= maxWidth) {
            return text;
        }
        let truncated = text;
        while (truncated.length > 0 && ctx.measureText(`${truncated}...`).width > maxWidth) {
            truncated = truncated.slice(0, -1);
        }
        return `${truncated}...`;
    }

    function drawBackground() {
        const gradient = ctx.createLinearGradient(0, 0, CANVAS_WIDTH, CANVAS_HEIGHT);
        gradient.addColorStop(0, "#111827");
        gradient.addColorStop(0.55, "#172033");
        gradient.addColorStop(1, "#0f766e");
        ctx.fillStyle = gradient;
        ctx.fillRect(0, 0, CANVAS_WIDTH, CANVAS_HEIGHT);

        ctx.fillStyle = "#1d4ed8";
        ctx.fillRect(0, 0, CANVAS_WIDTH, 6);
    }

    function drawTimer() {
        const startTimeMs = sceneData.startTimeMs || Date.now();
        const elapsed = Math.floor((Date.now() - startTimeMs) / 1000);
        const timeString = formatTime(elapsed);

        ctx.fillStyle = "#f9fafb";
        ctx.font = "bold 28px Arial";
        ctx.textAlign = "center";
        const title = truncateText(sceneData.meetingTitle || "Telemost Bot", CANVAS_WIDTH - 48, "bold 28px Arial");
        ctx.fillText(title, CANVAS_WIDTH / 2, 76);

        ctx.fillStyle = "#9ca3af";
        ctx.font = "18px Arial";
        ctx.fillText("meeting time", CANVAS_WIDTH / 2, CANVAS_HEIGHT / 2 - 58);

        ctx.fillStyle = "#34d399";
        ctx.font = "bold 86px monospace";
        ctx.fillText(timeString, CANVAS_WIDTH / 2, CANVAS_HEIGHT / 2 + 42);
    }

    function drawFooter() {
        const blinkOn = Math.floor(Date.now() / 500) % 2 === 0;
        ctx.fillStyle = "#1f2937";
        ctx.fillRect(0, CANVAS_HEIGHT - 34, CANVAS_WIDTH, 34);

        if (blinkOn) {
            ctx.fillStyle = "#ef4444";
            ctx.beginPath();
            ctx.arc(22, CANVAS_HEIGHT - 17, 7, 0, 2 * Math.PI);
            ctx.fill();
        }

        ctx.fillStyle = "#9ca3af";
        ctx.font = "bold 13px Arial";
        ctx.textAlign = "left";
        ctx.fillText("REC", 38, CANVAS_HEIGHT - 11);

        ctx.fillStyle = "#9ca3af";
        ctx.textAlign = "right";
        ctx.fillText("Telemost Bot", CANVAS_WIDTH - 14, CANVAS_HEIGHT - 11);
    }

    function render() {
        drawBackground();
        drawTimer();
        drawFooter();
    }

    function startRenderLoop() {
        if (renderLoopStarted) {
            return;
        }
        renderLoopStarted = true;
        render();
        setInterval(render, 1000);
    }

    window.__COMPOSITOR__ = {
        updateScene(data) {
            sceneData = Object.assign({}, sceneData, data || {});
            render();
        },
        getHealth() {
            return {
                version: window.__COMPOSITOR_VERSION__,
                hasData: Object.keys(sceneData).length > 0,
                startTimeMs: sceneData.startTimeMs,
                renderLoopStarted,
            };
        },
    };

    const originalGetUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);

    navigator.mediaDevices.getUserMedia = async function (constraints) {
        if (constraints && constraints.video) {
            startRenderLoop();
            const stream = canvas.captureStream(30);

            if (constraints.audio) {
                try {
                    const audioContext = new AudioContext();
                    const gainNode = audioContext.createGain();
                    gainNode.gain.value = 0;
                    const oscillator = audioContext.createOscillator();
                    const destination = audioContext.createMediaStreamDestination();
                    oscillator.connect(gainNode);
                    gainNode.connect(destination);
                    oscillator.start();
                    stream.addTrack(destination.stream.getAudioTracks()[0]);
                } catch (error) {
                    console.warn("[Compositor] Silent audio track failed:", error);
                }
            }

            return stream;
        }
        return originalGetUserMedia(constraints);
    };

    render();
    console.log("[Compositor] Injected");
})();
