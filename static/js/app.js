"use strict";

document.documentElement.classList.add("js-ready");

const escapeHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

const formatScenario = (value) => String(value || "LIVE").replaceAll("_", " ");

async function requestJson(url, options = {}, timeoutMs = 45000) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {
      ...options,
      signal: controller.signal,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || "The request could not be completed.");
    return payload;
  } catch (error) {
    if (error.name === "AbortError") throw new Error("This is taking longer than expected. Please try again.");
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}

function setupLanding() {
  const form = document.querySelector("[data-trip-form]");
  if (!form) return;
  const loader = document.querySelector("[data-create-loader]");
  const stage = document.querySelector("[data-loading-stage]");
  let stageTimer;

  const beginLoading = () => {
    const destination = form.elements.destination.value.trim().split(",")[0] || "your destination";
    const stages = [
      `Finding real places in ${destination}...`,
      "Reading destination context...",
      "Matching your travel style...",
      "Checking live conditions...",
      "Building a resilient itinerary...",
    ];
    let index = 0;
    loader.hidden = false;
    stage.textContent = stages[0];
    // Disabled controls are omitted from native form serialization. Keep every
    // named input enabled and disable only the submit action while navigating.
    form.querySelectorAll("button").forEach((element) => { element.disabled = true; });
    stageTimer = window.setInterval(() => {
      index = Math.min(index + 1, stages.length - 1);
      stage.textContent = stages[index];
      if (index === stages.length - 1) window.clearInterval(stageTimer);
    }, 4200);
  };

  form.addEventListener("submit", (event) => {
    if (!form.checkValidity()) return;
    const start = form.elements.start_date.value;
    const end = form.elements.end_date.value;
    if (end < start) {
      event.preventDefault();
      form.elements.end_date.setCustomValidity("Departure must be on or after arrival.");
      form.elements.end_date.reportValidity();
      return;
    }
    form.elements.end_date.setCustomValidity("");
    beginLoading();
  });

  form.elements.start_date.addEventListener("change", () => {
    form.elements.end_date.min = form.elements.start_date.value;
    if (form.elements.end_date.value < form.elements.start_date.value) {
      form.elements.end_date.value = form.elements.start_date.value;
    }
  });

  document.querySelectorAll("[data-preset]").forEach((button) => {
    button.addEventListener("click", () => {
      form.elements.destination.value = button.dataset.destination;
      const preferences = JSON.parse(button.dataset.preferences || "[]");
      form.querySelectorAll('input[name="preferences"]').forEach((input) => {
        input.checked = preferences.includes(input.value);
      });
      const pace = form.querySelector(`input[name="pace"][value="${button.dataset.pace}"]`);
      if (pace) pace.checked = true;
      form.requestSubmit();
    });
  });
}

function setupDashboard() {
  const dataElement = document.querySelector("#dashboard-data");
  if (!dataElement) return;
  const initial = JSON.parse(dataElement.textContent);
  const tripId = initial.trip.id;
  const planningMode = !initial.live_conditions_available;
  let activeScenario = initial.scenario || "LIVE";
  let latestTraceId = initial.applied?.trace_id || null;
  let repairBusy = false;
  let currentVulnerabilityCount = initial.active?.vulnerable_activity_count || 0;

  const scoreElement = document.querySelector("[data-resilience-score]");
  const scoreRing = document.querySelector("[data-score-ring]");
  const labelElement = document.querySelector("[data-resilience-label]");
  const vulnerabilityElement = document.querySelector("[data-vulnerability-count]");
  const summaryElement = document.querySelector("[data-resilience-summary]");
  const comparison = document.querySelector("[data-comparison]");
  const simulatedScore = document.querySelector("[data-simulated-score]");
  const conditionMode = document.querySelector("[data-condition-mode]");
  const modeKicker = document.querySelector("[data-mode-kicker]");
  const modeLabel = document.querySelector("[data-mode-label]");
  const repairButton = document.querySelector("[data-repair]");
  const repairLabel = document.querySelector("[data-repair-label]");
  const toast = document.querySelector("[data-toast]");

  // Future trips intentionally have no forecast, resilience, scenario, repair,
  // or condition-aware Ask Detour interactions to initialize.
  if (planningMode) return;

  function showToast(message) {
    toast.textContent = message;
    toast.hidden = false;
    window.setTimeout(() => { toast.hidden = true; }, 4500);
  }

  function updateActivity(evaluation) {
    const card = document.querySelector(`[data-item-id="${evaluation.itinerary_item_id}"]`);
    if (!card) return;
    card.classList.remove("status-go", "status-caution", "status-at-risk", "is-vulnerable", "is-updating");
    card.classList.add(`status-${evaluation.status.toLowerCase().replaceAll("_", "-")}`);
    card.classList.toggle("is-vulnerable", Boolean(evaluation.vulnerable));
    card.querySelector("[data-item-score]").textContent = evaluation.condition_score;
    card.querySelector("[data-item-status]").textContent = evaluation.status.replaceAll("_", " ");
    const reasons = card.querySelector("[data-item-reasons]");
    reasons.hidden = evaluation.status === "GO";
    reasons.innerHTML = evaluation.status === "GO" ? "" : evaluation.primary_risk_factors
      .map((reason) => `<span>${escapeHtml(reason)}</span>`).join("");
  }

  function updateResilience(result) {
    activeScenario = result.scenario;
    scoreElement.textContent = result.score;
    scoreRing.style.setProperty("--score", result.score);
    labelElement.textContent = result.label;
    const count = result.vulnerable_activity_count;
    currentVulnerabilityCount = count;
    vulnerabilityElement.textContent = `${count} ${count === 1 ? "vulnerability" : "vulnerabilities"}`;
    summaryElement.textContent = result.summary;
    result.item_evaluations.forEach(updateActivity);
    document.querySelectorAll("[data-scenario]").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.scenario === result.scenario);
    });
    conditionMode.classList.toggle("is-simulated", result.simulated);
    modeKicker.textContent = result.simulated ? "SIMULATED SCENARIO" : "CURRENT SIGNAL";
    modeLabel.textContent = result.simulated ? `SIMULATED: ${formatScenario(result.scenario)}` : "LIVE CONDITIONS";
    comparison.hidden = !result.simulated;
    if (result.simulated) simulatedScore.textContent = result.score;
    repairButton.disabled = count === 0;
    repairLabel.textContent = count ? "Repair my trip" : "No repair needed";
    const url = new URL(window.location.href);
    if (result.simulated) url.searchParams.set("scenario", result.scenario);
    else url.searchParams.delete("scenario");
    url.searchParams.delete("applied");
    window.history.replaceState({}, "", url);
  }

  document.querySelectorAll("[data-scenario]").forEach((button) => {
    button.addEventListener("click", async () => {
      if (button.dataset.scenario === activeScenario) return;
      const controls = document.querySelectorAll("[data-scenario]");
      controls.forEach((control) => { control.disabled = true; });
      document.querySelectorAll("[data-item-id]").forEach((card) => card.classList.add("is-updating"));
      try {
        const query = button.dataset.scenario === "LIVE" ? "" : `?scenario=${button.dataset.scenario}`;
        updateResilience(await requestJson(`/api/trips/${tripId}/resilience${query}`));
      } catch (error) {
        document.querySelectorAll("[data-item-id]").forEach((card) => card.classList.remove("is-updating"));
        showToast(error.message);
      } finally {
        controls.forEach((control) => { control.disabled = false; });
      }
    });
  });

  const repairModal = document.querySelector("[data-repair-modal]");
  const repairProgress = document.querySelector("[data-repair-progress]");
  const repairResult = document.querySelector("[data-repair-result]");
  const closeRepair = document.querySelector("[data-close-repair]");
  let progressTimer;

  function openRepairProgress() {
    repairModal.hidden = false;
    repairProgress.hidden = false;
    repairResult.hidden = true;
    closeRepair.hidden = true;
    document.body.style.overflow = "hidden";
    const stages = [...repairProgress.querySelectorAll(".progress-stages li")];
    let index = 0;
    stages.forEach((stage, stageIndex) => stage.classList.toggle("is-active", stageIndex === 0));
    progressTimer = window.setInterval(() => {
      stages[index].classList.remove("is-active");
      index = (index + 1) % stages.length;
      stages[index].classList.add("is-active");
    }, 4300);
  }

  function closeRepairModal() {
    if (repairBusy) return;
    repairModal.hidden = true;
    document.body.style.overflow = "";
    repairButton.disabled = currentVulnerabilityCount === 0;
  }

  function renderProposal(proposal) {
    const impact = proposal.impact;
    const actions = proposal.actions.map((action) => {
      const before = action.before;
      const after = action.after;
      const beforeEval = action.before_evaluation;
      const afterEval = action.after_evaluation;
      return `
        <article class="diff-card">
          <span class="diff-type">${escapeHtml(action.action_type)}</span>
          <div class="diff-row">
            <div class="diff-side before">
              <small>Before</small>
              <strong>${escapeHtml(before.title)}</strong>
              <span>${escapeHtml(before.day_date)} · ${escapeHtml(before.start_time)} · ${escapeHtml(beforeEval.status.replaceAll("_", " "))} ${beforeEval.condition_score}</span>
            </div>
            <span class="diff-arrow">→</span>
            <div class="diff-side after">
              <small>After</small>
              <strong>${escapeHtml(after.title)}</strong>
              <span>${escapeHtml(after.day_date)} · ${escapeHtml(after.start_time)} · ${escapeHtml(afterEval.status.replaceAll("_", " "))} ${afterEval.condition_score}</span>
            </div>
          </div>
          <p class="diff-reason"><strong>Why:</strong> ${escapeHtml(action.reason)}</p>
        </article>`;
    }).join("");

    repairResult.innerHTML = `
      <div class="proposal-header">
        <p class="section-kicker">Proposed Detour</p>
        <h2>Risk reduced, intent preserved.</h2>
        <p>${escapeHtml(proposal.rationale)}</p>
      </div>
      <div class="impact-grid">
        <div><span>Resilience</span><strong>${proposal.resilience_before} → ${proposal.resilience_projected}</strong></div>
        <div class="impact-positive"><span>Risks resolved</span><strong>${impact.risks_resolved}</strong></div>
        <div><span>Risks remaining</span><strong>${impact.risks_remaining}</strong></div>
      </div>
      <div class="diff-list">${actions}</div>
      <div class="proposal-actions">
        <button class="primary-button" type="button" data-apply-repair><span>Apply Detour</span><span>→</span></button>
        <button class="secondary-button" type="button" data-dismiss-repair>Keep original</button>
      </div>
      <div class="proposal-links"><button class="link-button" type="button" data-open-trace data-trace-id="${escapeHtml(proposal.trace_id)}">View agent trace</button></div>`;
    repairProgress.hidden = true;
    repairResult.hidden = false;
    closeRepair.hidden = false;
    repairResult.querySelector("[data-dismiss-repair]").addEventListener("click", closeRepairModal);
    repairResult.querySelector("[data-apply-repair]").addEventListener("click", async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      button.querySelector("span").textContent = "Applying...";
      try {
        await requestJson(`/api/repairs/${proposal.repair_id}/apply`, { method: "POST", body: "{}" }, 60000);
        const destination = new URL(`/trips/${tripId}`, window.location.origin);
        if (activeScenario !== "LIVE") destination.searchParams.set("scenario", activeScenario);
        destination.searchParams.set("applied", proposal.repair_id);
        window.location.assign(destination);
      } catch (error) {
        button.disabled = false;
        button.querySelector("span").textContent = "Apply Detour";
        showToast(error.message);
      }
    });
  }

  repairButton.addEventListener("click", async () => {
    if (repairBusy || repairButton.disabled) return;
    repairBusy = true;
    repairButton.disabled = true;
    openRepairProgress();
    try {
      const result = await requestJson(
        `/api/trips/${tripId}/repair`,
        { method: "POST", body: JSON.stringify({ scenario: activeScenario }) },
        180000,
      );
      window.clearInterval(progressTimer);
      latestTraceId = result.trace_id;
      repairBusy = false;
      renderProposal(result.proposal);
    } catch (error) {
      window.clearInterval(progressTimer);
      repairBusy = false;
      repairProgress.hidden = true;
      repairResult.hidden = false;
      closeRepair.hidden = false;
      repairResult.innerHTML = `<div class="repair-error"><span>!</span><h2>No changes were made.</h2><p>${escapeHtml(error.message)}</p><button class="secondary-button" type="button" data-dismiss-repair>Return to itinerary</button></div>`;
      repairResult.querySelector("[data-dismiss-repair]").addEventListener("click", closeRepairModal);
      repairButton.disabled = false;
    }
  });
  closeRepair.addEventListener("click", closeRepairModal);

  const traceDrawer = document.querySelector("[data-trace-drawer]");
  const traceContent = document.querySelector("[data-trace-content]");
  const traceLabels = {
    AGENT_STARTED: "Agent started",
    MODEL_REQUEST: "Asked Llama for the next action",
    MODEL_RESPONSE: "Received model response",
    TOOL_CALLED: "Called a Detour tool",
    TOOL_COMPLETED: "Tool completed",
    RETRIEVAL_COMPLETED: "Retrieved destination alternatives",
    REPAIR_PROPOSED: "Saved pending repair",
    AGENT_COMPLETED: "Agent completed",
    DETERMINISTIC_FALLBACK_STARTED: "Guarded fallback started",
    DETERMINISTIC_FALLBACK_COMPLETED: "Guarded fallback completed",
    REPAIR_APPLY_STARTED: "Apply transaction started",
    REPAIR_ACTION_APPLIED: "Applied itinerary action",
    REPAIR_APPLIED: "Repair applied",
    AGENT_ERROR: "Agent stopped safely",
    REPAIR_APPLY_ERROR: "Apply transaction rolled back",
  };

  function renderTrace(payload) {
    const seconds = (payload.summary.observed_duration_ms / 1000).toFixed(1);
    const events = payload.events.map((event) => {
      const validation = event.output_summary?.validation_summary;
      const detail = validation || (event.tool_name ? event.tool_name.replaceAll("_", " ") : "");
      return `<div class="trace-event ${event.status === "error" ? "is-error" : ""}">
        <span class="trace-status">${event.status === "error" ? "!" : "✓"}</span>
        <div><strong>${escapeHtml(traceLabels[event.event_type] || event.event_type.replaceAll("_", " "))}</strong>${detail ? `<p>${escapeHtml(detail)}</p>` : ""}</div>
        <time>${event.duration_ms != null ? `${event.duration_ms}ms` : ""}</time>
      </div>`;
    }).join("");
    traceContent.innerHTML = `
      <p class="trace-id">Trace #${escapeHtml(payload.trace_id.slice(0, 8).toUpperCase())}</p>
      <div class="trace-summary">
        <div><strong>${payload.summary.tool_calls}</strong><span>tool calls</span></div>
        <div><strong>${payload.summary.model_calls}</strong><span>model calls</span></div>
        <div><strong>${seconds}s</strong><span>observed time</span></div>
      </div>
      <div class="trace-timeline">${events}</div>`;
  }

  async function openTrace(traceId) {
    if (!traceId) return;
    traceDrawer.hidden = false;
    document.body.style.overflow = "hidden";
    traceContent.innerHTML = '<div class="mini-loader"></div>';
    try {
      renderTrace(await requestJson(`/api/traces/${encodeURIComponent(traceId)}`));
    } catch (error) {
      traceContent.innerHTML = `<div class="repair-error"><span>!</span><p>${escapeHtml(error.message)}</p></div>`;
    }
  }

  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-open-trace]");
    if (trigger) openTrace(trigger.dataset.traceId || latestTraceId);
  });
  document.querySelector("[data-close-trace]").addEventListener("click", () => {
    traceDrawer.hidden = true;
    document.body.style.overflow = repairModal.hidden ? "" : "hidden";
  });

  const askForm = document.querySelector("[data-ask-form]");
  const askResponse = document.querySelector("[data-ask-response]");
  askForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const question = askForm.elements.question.value.trim();
    if (!question) return;
    const button = askForm.querySelector("button");
    button.disabled = true;
    button.textContent = "…";
    askResponse.hidden = false;
    askResponse.textContent = "Reading your itinerary and condition signals...";
    try {
      const result = await requestJson(
        `/api/trips/${tripId}/ask`,
        { method: "POST", body: JSON.stringify({ question, scenario: activeScenario }) },
        150000,
      );
      askResponse.textContent = result.answer;
    } catch (error) {
      askResponse.textContent = error.message;
    } finally {
      button.disabled = false;
      button.textContent = "→";
    }
  });
}

setupLanding();
setupDashboard();
