import fs from "node:fs";
import path from "node:path";

const API_BASE = "https://api.pyannote.ai/v1";
const DEFAULT_MODEL = "precision-2";
const DEFAULT_POLL_INTERVAL_MS = 10000;
const DEFAULT_TIMEOUT_MS = 30 * 60 * 1000;

function fail(message) {
  console.error(message);
  process.exit(1);
}

function parseArgs(argv) {
  const options = {
    speakers: 2,
    transcription: true,
    exclusive: true,
    pollIntervalMs: DEFAULT_POLL_INTERVAL_MS,
    timeoutMs: DEFAULT_TIMEOUT_MS,
  };

  for (let i = 2; i < argv.length; i += 1) {
    const value = argv[i];

    if (!value.startsWith("--") && !options.audioPath) {
      options.audioPath = value;
      continue;
    }

    if (value === "--speakers") {
      options.speakers = Number(argv[++i]);
      continue;
    }

    if (value === "--no-transcription") {
      options.transcription = false;
      continue;
    }

    if (value === "--no-exclusive") {
      options.exclusive = false;
      continue;
    }

    if (value === "--poll-ms") {
      options.pollIntervalMs = Number(argv[++i]);
      continue;
    }

    if (value === "--timeout-ms") {
      options.timeoutMs = Number(argv[++i]);
      continue;
    }

    fail(`Unknown argument: ${value}`);
  }

  if (!options.audioPath) {
    fail("Usage: node run-pyannote-job.mjs <audioPath> [--speakers 2] [--no-transcription]");
  }

  if (!Number.isFinite(options.speakers) || options.speakers < 1) {
    fail("`--speakers` must be a positive number.");
  }

  if (!Number.isFinite(options.pollIntervalMs) || options.pollIntervalMs < 1000) {
    fail("`--poll-ms` must be at least 1000.");
  }

  if (!Number.isFinite(options.timeoutMs) || options.timeoutMs < 1000) {
    fail("`--timeout-ms` must be at least 1000.");
  }

  return options;
}

function buildObjectKey(audioPath) {
  const fileName = path.basename(audioPath);
  const safeName = fileName.replace(/[^a-zA-Z0-9._-]+/g, "_");
  return `telemost/${new Date().toISOString().replace(/[:.]/g, "-")}_${safeName}`;
}

async function pyannoteRequest(endpoint, apiKey, init = {}) {
  const response = await fetch(`${API_BASE}${endpoint}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${apiKey}`,
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(init.headers ?? {}),
    },
  });

  const text = await response.text();
  let data = null;

  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }

  if (!response.ok) {
    const detail = typeof data === "string" ? data : JSON.stringify(data, null, 2);
    throw new Error(`pyannote ${endpoint} failed: ${response.status} ${response.statusText}\n${detail}`);
  }

  return data;
}

async function uploadMedia(audioPath, apiKey, objectKey) {
  const signedUpload = await pyannoteRequest("/media/input", apiKey, {
    method: "POST",
    body: JSON.stringify({ url: `media://${objectKey}` }),
  });

  const fileBuffer = await fs.promises.readFile(audioPath);
  const uploadResponse = await fetch(signedUpload.url, {
    method: "PUT",
    headers: { "Content-Type": "application/octet-stream" },
    body: fileBuffer,
  });

  if (!uploadResponse.ok) {
    const detail = await uploadResponse.text();
    throw new Error(`Upload to signed URL failed: ${uploadResponse.status} ${uploadResponse.statusText}\n${detail}`);
  }

  return `media://${objectKey}`;
}

async function createJob(mediaUrl, apiKey, options) {
  const payload = {
    url: mediaUrl,
    model: DEFAULT_MODEL,
    numSpeakers: options.speakers,
    exclusive: options.exclusive,
    transcription: options.transcription,
  };

  return pyannoteRequest("/diarize", apiKey, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

async function waitForJob(jobId, apiKey, options) {
  const startedAt = Date.now();

  while (Date.now() - startedAt < options.timeoutMs) {
    const job = await pyannoteRequest(`/jobs/${jobId}`, apiKey);
    const status = String(job.status ?? "").toLowerCase();

    if (status === "failed" || job.output?.error) {
      throw new Error(`Job ${jobId} failed.\n${JSON.stringify(job, null, 2)}`);
    }

    if (status === "succeeded" || status === "completed" || job.output?.diarization || job.output?.turnLevelTranscription) {
      return job;
    }

    console.log(`[pyannote] job ${jobId} status: ${job.status ?? "processing"}`);
    await new Promise((resolve) => setTimeout(resolve, options.pollIntervalMs));
  }

  throw new Error(`Timeout waiting for job ${jobId}`);
}

function renderTranscript(job) {
  const turns = job.output?.turnLevelTranscription;
  if (!Array.isArray(turns) || turns.length === 0) {
    return "";
  }

  return turns
    .map((turn) => {
      const start = Number(turn.start ?? 0).toFixed(1);
      const end = Number(turn.end ?? 0).toFixed(1);
      const speaker = turn.speaker ?? "UNKNOWN";
      const text = String(turn.text ?? "").trim();
      return `[${start}-${end}] ${speaker}: ${text}`;
    })
    .join("\n");
}

async function main() {
  const options = parseArgs(process.argv);
  const apiKey = process.env.PYANNOTE_API_KEY;

  if (!apiKey) {
    fail("Set PYANNOTE_API_KEY before running the script.");
  }

  const audioPath = path.resolve(options.audioPath);
  if (!fs.existsSync(audioPath)) {
    fail(`Audio file not found: ${audioPath}`);
  }

  const objectKey = buildObjectKey(audioPath);
  const mediaUrl = await uploadMedia(audioPath, apiKey, objectKey);
  console.log(`[pyannote] uploaded: ${mediaUrl}`);

  const createdJob = await createJob(mediaUrl, apiKey, options);
  const jobId = createdJob.jobId;
  if (!jobId) {
    throw new Error(`No jobId returned.\n${JSON.stringify(createdJob, null, 2)}`);
  }

  console.log(`[pyannote] job created: ${jobId}`);
  const completedJob = await waitForJob(jobId, apiKey, options);

  const outputBase = path.join(path.dirname(audioPath), `${path.parse(audioPath).name}_pyannote_${jobId}`);
  const jsonPath = `${outputBase}.json`;
  const txtPath = `${outputBase}.txt`;

  await fs.promises.writeFile(jsonPath, JSON.stringify(completedJob, null, 2), "utf8");

  const transcript = renderTranscript(completedJob);
  if (transcript) {
    await fs.promises.writeFile(txtPath, transcript, "utf8");
  }

  console.log(`[pyannote] saved job json: ${jsonPath}`);
  if (transcript) {
    console.log(`[pyannote] saved transcript: ${txtPath}`);
  } else {
    console.log("[pyannote] transcript not returned, only diarization json was saved.");
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
});
