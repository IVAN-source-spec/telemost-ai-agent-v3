// Synthetic camera compositor for Telemost.
// It replaces camera video with a canvas timer stream.
(function () {
    "use strict";

    if (window.__COMPOSITOR__) {
        return;
    }

    window.__COMPOSITOR_VERSION__ = 3;

    const CANVAS_WIDTH = 1280;
    const CANVAS_HEIGHT = 720;
    const FOOTER_HEIGHT = 50;
    const CONTENT_HEIGHT = CANVAS_HEIGHT - FOOTER_HEIGHT;
    const HOUR_SECONDS = 3600;
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
        return hours > 0 ? `${hours}:${pad(minutes)}:${pad(seconds)}` : `${pad(minutes)}:${pad(seconds)}`;
    }

    function wrapText(text, maxWidth, font, maxLines) {
        ctx.font = font;
        const words = String(text || "").trim().split(/\s+/).filter(Boolean);
        const lines = [];
        let current = "";
        for (const word of words) {
            const candidate = current ? `${current} ${word}` : word;
            if (ctx.measureText(candidate).width <= maxWidth || !current) {
                current = candidate;
            } else {
                lines.push(current);
                current = word;
                if (lines.length >= maxLines - 1) {
                    break;
                }
            }
        }
        if (current && lines.length < maxLines) {
            lines.push(current);
        }
        if (words.length && lines.length === maxLines) {
            let last = lines[lines.length - 1];
            while (last.length > 0 && ctx.measureText(`${last}...`).width > maxWidth) {
                last = last.slice(0, -1);
            }
            lines[lines.length - 1] = `${last}...`;
        }
        return lines;
    }

    function roundRect(x, y, w, h, r) {
        const radius = Math.min(r, w / 2, h / 2);
        ctx.beginPath();
        ctx.moveTo(x + radius, y);
        ctx.arcTo(x + w, y, x + w, y + h, radius);
        ctx.arcTo(x + w, y + h, x, y + h, radius);
        ctx.arcTo(x, y + h, x, y, radius);
        ctx.arcTo(x, y, x + w, y, radius);
        ctx.closePath();
    }

    function fillRoundRect(x, y, w, h, r, fillStyle) {
        ctx.fillStyle = fillStyle;
        roundRect(x, y, w, h, r);
        ctx.fill();
    }

    function strokeRoundRect(x, y, w, h, r, strokeStyle, lineWidth) {
        ctx.strokeStyle = strokeStyle;
        ctx.lineWidth = lineWidth;
        roundRect(x, y, w, h, r);
        ctx.stroke();
    }

    function drawBackground() {
        const gradient = ctx.createLinearGradient(0, 0, CANVAS_WIDTH, CANVAS_HEIGHT);
        gradient.addColorStop(0, "#080d11");
        gradient.addColorStop(0.52, "#0b1117");
        gradient.addColorStop(1, "#101720");
        ctx.fillStyle = gradient;
        ctx.fillRect(0, 0, CANVAS_WIDTH, CANVAS_HEIGHT);
    }

    function drawCard(x, y, w, h, radius = 18) {
        const gradient = ctx.createLinearGradient(x, y, x + w, y + h);
        gradient.addColorStop(0, "#18212a");
        gradient.addColorStop(1, "#101923");
        fillRoundRect(x, y, w, h, radius, gradient);
        strokeRoundRect(x, y, w, h, radius, "rgba(255,255,255,0.045)", 1.5);
    }

    function fontThatFits(text, maxWidth, preferredPx, minPx, weight = "bold") {
        let size = preferredPx;
        while (size > minPx) {
            ctx.font = `${weight} ${size}px Arial`;
            if (ctx.measureText(text).width <= maxWidth) {
                return ctx.font;
            }
            size -= 1;
        }
        ctx.font = `${weight} ${minPx}px Arial`;
        return ctx.font;
    }

    function drawMeetingCircle(cx, cy, radius, elapsedSeconds, compact) {
        const fraction = (Math.max(0, elapsedSeconds) % HOUR_SECONDS) / HOUR_SECONDS;
        const startAngle = -Math.PI / 2;
        const endAngle = startAngle + Math.max(0.012, fraction) * Math.PI * 2;

        ctx.lineWidth = compact ? 16 : 24;
        ctx.lineCap = "round";
        ctx.strokeStyle = "#33404b";
        ctx.beginPath();
        ctx.arc(cx, cy, radius, 0, Math.PI * 2);
        ctx.stroke();

        const arcGradient = ctx.createLinearGradient(cx - radius, cy - radius, cx + radius, cy + radius);
        arcGradient.addColorStop(0, "#55e99a");
        arcGradient.addColorStop(1, "#16a34a");
        ctx.strokeStyle = arcGradient;
        ctx.beginPath();
        ctx.arc(cx, cy, radius, startAngle, endAngle);
        ctx.stroke();
        ctx.lineCap = "butt";

        const timeText = formatTime(elapsedSeconds);
        ctx.textAlign = "center";
        ctx.fillStyle = "#f8fafc";
        ctx.font = fontThatFits(timeText, radius * 1.42, compact ? 45 : 70, compact ? 28 : 44);
        ctx.fillText(timeText, cx, cy + (compact ? 12 : 18));
        ctx.fillStyle = "#f8fafc";
        ctx.font = compact ? "bold 18px Arial" : "bold 26px Arial";
        ctx.fillText("ПРОШЛО", cx, cy + (compact ? 44 : 62));
    }

    function meetingElapsedSeconds() {
        const startTimeMs = sceneData.startTimeMs || Date.now();
        return Math.floor((Date.now() - startTimeMs) / 1000);
    }

    function agendaQuestionElapsedSeconds() {
        const segmentElapsed = Math.floor((Date.now() - (sceneData.agendaQuestionStartTimeMs || Date.now())) / 1000);
        return Number(sceneData.agendaAccumulatedSeconds || 0) + segmentElapsed;
    }

    function drawPlainMeetingTimer(elapsed) {
        drawCard(22, 22, CANVAS_WIDTH - 44, CONTENT_HEIGHT - 44, 18);
        ctx.textAlign = "center";
        ctx.fillStyle = "#8c98a5";
        ctx.font = "bold 29px Arial";
        ctx.fillText("ВСТРЕЧА", CANVAS_WIDTH / 2, 86);

        const title = (sceneData.meetingTitle || "Telemost Bot").toUpperCase();
        const titleLines = wrapText(title, CANVAS_WIDTH - 180, "bold 44px Arial", 2);
        ctx.fillStyle = "#f8fafc";
        ctx.font = "bold 44px Arial";
        titleLines.forEach((line, index) => ctx.fillText(line, CANVAS_WIDTH / 2, 136 + index * 52));

        drawMeetingCircle(CANVAS_WIDTH / 2, 378, 150, elapsed, false);
    }

    function drawQuestionProgress(x, y, w, h, elapsed, plannedSeconds) {
        const hasPlan = plannedSeconds > 0;
        fillRoundRect(x, y, w, h, 14, "#34434f");

        if (!hasPlan) {
            const progressW = Math.max(20, Math.min(w, w * ((elapsed % HOUR_SECONDS) / HOUR_SECONDS)));
            fillRoundRect(x, y, progressW, h, 14, "#16a34a");
            ctx.fillStyle = "#f8fafc";
            ctx.font = fontThatFits(`${formatTime(elapsed)} ПРОШЛО`, w - 40, 28, 20);
            ctx.textAlign = "center";
            ctx.fillText(`${formatTime(elapsed)} ПРОШЛО`, x + w / 2, y + h / 2 + 10);
            return;
        }

        const overrun = Math.max(0, elapsed - plannedSeconds);
        const within = Math.min(elapsed, plannedSeconds);
        const greenW = overrun > 0 ? w : Math.max(0, Math.min(w, w * (within / plannedSeconds)));
        if (greenW > 0) {
            const greenGradient = ctx.createLinearGradient(x, y, x + greenW, y);
            greenGradient.addColorStop(0, "#23c46a");
            greenGradient.addColorStop(1, "#16a34a");
            fillRoundRect(x, y, greenW, h, 14, greenGradient);
        }

        if (overrun > 0) {
            const redW = Math.max(16, Math.min(w, w * (overrun / plannedSeconds)));
            const redGradient = ctx.createLinearGradient(x + w - redW, y, x + w, y);
            redGradient.addColorStop(0, "#fb923c");
            redGradient.addColorStop(1, "#dc2626");
            fillRoundRect(x + w - redW, y, redW, h, 14, redGradient);
        }

        ctx.fillStyle = "#f8fafc";
        ctx.textAlign = "center";
        const elapsedLabel = overrun > 0 ? `${formatTime(plannedSeconds)} ЛИМИТ` : `${formatTime(elapsed)} ПРОШЛО`;
        const remainingLabel = overrun > 0 ? `+${formatTime(overrun)} ПЕРЕРАСХОД` : `${formatTime(plannedSeconds - elapsed)} ОСТАЛОСЬ`;
        ctx.font = "bold 24px Arial";
        const halfWidth = w * 0.44;
        if (ctx.measureText(elapsedLabel).width > halfWidth || ctx.measureText(remainingLabel).width > halfWidth) {
            ctx.font = "bold 21px Arial";
        }
        ctx.fillText(elapsedLabel, x + w * 0.26, y + h / 2 + 9);
        ctx.fillText(remainingLabel, x + w * 0.74, y + h / 2 + 9);
    }

    function drawAgendaSplit(elapsed, questionElapsed, plannedSeconds) {
        drawCard(22, 22, CANVAS_WIDTH - 44, CONTENT_HEIGHT - 44, 18);

        const leftX = 54;
        const topY = 54;
        const leftW = 790;
        const leftH = 540;
        const rightX = 876;
        const rightW = 350;
        const rightH = 540;
        drawCard(leftX, topY, leftW, leftH, 14);
        drawCard(rightX, topY, rightW, rightH, 14);

        ctx.textAlign = "left";
        ctx.fillStyle = "#8c98a5";
        ctx.font = "bold 25px Arial";
        ctx.fillText(`ВОПРОС ${sceneData.agendaIndex || 1} / ${sceneData.agendaTotal || 1}`, leftX + 34, topY + 58);

        const title = (sceneData.agendaTitle || "Текущий вопрос").toUpperCase();
        const titleLines = wrapText(title, leftW - 68, "bold 43px Arial", 3);
        ctx.fillStyle = "#f8fafc";
        ctx.font = "bold 43px Arial";
        titleLines.forEach((line, index) => ctx.fillText(line, leftX + 34, topY + 116 + index * 50));

        ctx.fillStyle = "#8c98a5";
        ctx.font = "bold 21px Arial";
        const limitText = plannedSeconds > 0 ? `ВОПРОС · ЛИМИТ ${formatTime(plannedSeconds)}` : "ВОПРОС · БЕЗ ЛИМИТА";
        ctx.fillText(limitText, leftX + 34, topY + 386);
        drawQuestionProgress(leftX + 34, topY + 410, leftW - 68, 62, questionElapsed, plannedSeconds);

        ctx.textAlign = "center";
        ctx.fillStyle = "#8c98a5";
        ctx.font = "bold 25px Arial";
        ctx.fillText("ВСТРЕЧА", rightX + rightW / 2, topY + 64);
        drawMeetingCircle(rightX + rightW / 2, topY + 280, 110, elapsed, true);
    }

    function drawTimer() {
        const elapsed = meetingElapsedSeconds();
        if (!sceneData.agendaEnabled) {
            drawPlainMeetingTimer(elapsed);
            return;
        }
        const questionElapsed = agendaQuestionElapsedSeconds();
        const plannedSeconds = Number(sceneData.agendaPlannedSeconds || 0);
        drawAgendaSplit(elapsed, questionElapsed, plannedSeconds);
    }

    function drawFooter() {
        const blinkOn = Math.floor(Date.now() / 500) % 2 === 0;
        ctx.fillStyle = "#16202a";
        ctx.fillRect(0, CANVAS_HEIGHT - FOOTER_HEIGHT, CANVAS_WIDTH, FOOTER_HEIGHT);

        if (blinkOn) {
            ctx.fillStyle = "#ef4444";
            ctx.beginPath();
            ctx.arc(36, CANVAS_HEIGHT - 25, 9, 0, 2 * Math.PI);
            ctx.fill();
        }

        ctx.fillStyle = "#9ca3af";
        ctx.font = "bold 18px Arial";
        ctx.textAlign = "left";
        ctx.fillText("REC", 58, CANVAS_HEIGHT - 18);

        ctx.fillStyle = "#9ca3af";
        ctx.textAlign = "right";
        ctx.fillText("Telemost Bot", CANVAS_WIDTH - 28, CANVAS_HEIGHT - 18);
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
                canvasWidth: CANVAS_WIDTH,
                canvasHeight: CANVAS_HEIGHT,
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