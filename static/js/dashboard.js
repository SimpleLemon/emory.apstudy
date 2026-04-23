const CALENDAR_BASE_HEIGHT_PX = 680;
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

const state = {
	events: [],
	feedConfigured: false,
	view: "week",
	anchorDate: new Date(),
};

document.addEventListener("DOMContentLoaded", () => {
	wireControls();
	loadCalendarData();
});

async function loadCalendarData() {
	try {
		const statusRes = await fetch("/api/calendar/status", { credentials: "same-origin" });
		if (!statusRes.ok) {
			throw new Error("Unable to fetch calendar status");
		}

		const status = await statusRes.json();
		state.feedConfigured = Boolean(status.feed_configured);

		if (!state.feedConfigured) {
			state.events = [];
			render();
			return;
		}

		await fetch("/api/calendar/refresh", {
			method: "POST",
			credentials: "same-origin",
		}).catch(() => null);

		const eventsRes = await fetch("/api/calendar/events", { credentials: "same-origin" });
		if (!eventsRes.ok) {
			throw new Error("Unable to fetch calendar events");
		}

		const payload = await eventsRes.json();
		state.events = Array.isArray(payload.events)
			? payload.events
					.filter((event) => event.start)
					.map((event) => ({
						...event,
						startDate: new Date(event.start),
						endDate: event.end ? new Date(event.end) : new Date(event.start),
					}))
					.sort((a, b) => a.startDate - b.startDate)
			: [];

		render();
	} catch (error) {
		state.feedConfigured = false;
		state.events = [];
		render();
		console.error(error);
	}
}

function wireControls() {
	document.getElementById("calendar-view-week")?.addEventListener("click", () => {
		state.view = "week";
		render();
	});

	document.getElementById("calendar-view-month")?.addEventListener("click", () => {
		state.view = "month";
		render();
	});

	document.getElementById("calendar-prev")?.addEventListener("click", () => {
		state.anchorDate = shiftAnchorDate(-1);
		render();
	});

	document.getElementById("calendar-next")?.addEventListener("click", () => {
		state.anchorDate = shiftAnchorDate(1);
		render();
	});

	document.getElementById("calendar-today")?.addEventListener("click", () => {
		state.anchorDate = new Date();
		render();
	});
}

function shiftAnchorDate(delta) {
	const next = new Date(state.anchorDate);
	if (state.view === "month") {
		next.setMonth(next.getMonth() + delta);
		return next;
	}
	next.setDate(next.getDate() + delta * 7);
	return next;
}

function render() {
	updateHeader();
	updateViewToggleButtons();
	renderCalendarView();
	renderAssignments();
}

function updateHeader() {
	const title = document.getElementById("calendar-title");
	const subtitle = document.getElementById("calendar-subtitle");
	if (!title || !subtitle) {
		return;
	}

	const monthLabel = state.anchorDate.toLocaleDateString(undefined, {
		month: "long",
		year: "numeric",
	});

	if (state.view === "month") {
		title.textContent = monthLabel;
		subtitle.textContent = state.feedConfigured
			? "Monthly view from your saved iCal feed"
			: "Monthly view (no .ics feed connected yet)";
		return;
	}

	const start = getStartOfWeek(state.anchorDate);
	const end = new Date(start);
	end.setDate(end.getDate() + 6);

	title.textContent = `Week of ${start.toLocaleDateString(undefined, {
		month: "short",
		day: "numeric",
	})} - ${end.toLocaleDateString(undefined, { month: "short", day: "numeric" })}`;
	subtitle.textContent = state.feedConfigured
		? `Weekly view (${monthLabel}) from your saved iCal feed`
		: `Weekly view (${monthLabel}) with no .ics feed connected`;
}

function updateViewToggleButtons() {
	const weekBtn = document.getElementById("calendar-view-week");
	const monthBtn = document.getElementById("calendar-view-month");

	if (!weekBtn || !monthBtn) {
		return;
	}

	const isWeek = state.view === "week";
	weekBtn.className = isWeek
		? "px-3 py-1.5 text-sm rounded-md bg-primary text-on-primary font-medium"
		: "px-3 py-1.5 text-sm rounded-md text-on-surface-variant hover:text-on-surface";

	monthBtn.className = !isWeek
		? "px-3 py-1.5 text-sm rounded-md bg-primary text-on-primary font-medium"
		: "px-3 py-1.5 text-sm rounded-md text-on-surface-variant hover:text-on-surface";
}

function renderCalendarView() {
	const root = document.getElementById("calendar-view-root");
	if (!root) {
		return;
	}

	root.innerHTML = state.view === "month" ? buildMonthViewHtml() : buildWeekViewHtml();
}

function buildMonthViewHtml() {
	const monthStart = new Date(state.anchorDate.getFullYear(), state.anchorDate.getMonth(), 1);
	const monthEnd = new Date(state.anchorDate.getFullYear(), state.anchorDate.getMonth() + 1, 0);
	const gridStart = new Date(monthStart);
	gridStart.setDate(monthStart.getDate() - monthStart.getDay());

	const cells = [];
	for (let i = 0; i < 42; i += 1) {
		const day = new Date(gridStart);
		day.setDate(gridStart.getDate() + i);
		const inMonth = day >= monthStart && day <= monthEnd;
		const dayEvents = getEventsForDay(day);

		cells.push(`
			<div class="rounded-lg border ${inMonth ? "border-outline-variant/10 bg-surface-container" : "border-outline-variant/5 bg-surface-container/40 opacity-70"} p-2.5 min-h-[98px] flex flex-col gap-1.5">
				<div class="text-xs font-semibold ${isToday(day) ? "text-primary" : "text-on-surface-variant"}">${day.getDate()}</div>
				<div class="space-y-1">
					${dayEvents.slice(0, 3).map((event) => buildEventChip(event)).join("")}
					${dayEvents.length > 3 ? `<div class="text-[10px] text-on-surface-variant">+${dayEvents.length - 3} more</div>` : ""}
				</div>
			</div>
		`);
	}

	return `
		<div class="grid grid-cols-7 gap-2 mb-2">
			${WEEKDAYS.map((day) => `<div class="text-[11px] uppercase tracking-[0.05em] text-center text-on-surface-variant font-semibold">${day}</div>`).join("")}
		</div>
		<div class="grid grid-cols-7 gap-2" style="min-height:${CALENDAR_BASE_HEIGHT_PX}px;">
			${cells.join("")}
		</div>
	`;
}

function buildWeekViewHtml() {
	const weekStart = getStartOfWeek(state.anchorDate);
	const dayBlocks = [];

	for (let i = 0; i < 7; i += 1) {
		const day = new Date(weekStart);
		day.setDate(weekStart.getDate() + i);
		const dayEvents = getEventsForDay(day);
		const dayLabel = day.toLocaleDateString(undefined, {
			weekday: "long",
			month: "short",
			day: "numeric",
		});

		dayBlocks.push(`
			<article class="rounded-xl border border-outline-variant/10 bg-surface-container p-3">
				<header class="flex items-center justify-between mb-2">
					<h3 class="text-sm font-semibold ${isToday(day) ? "text-primary" : "text-on-surface"}">${dayLabel}</h3>
					<span class="text-xs text-on-surface-variant">${dayEvents.length} item${dayEvents.length === 1 ? "" : "s"}</span>
				</header>
				<div class="space-y-1.5">
					${dayEvents.length ? dayEvents.map((event) => buildEventChip(event)).join("") : '<div class="text-xs text-on-surface-variant">No assignments</div>'}
				</div>
			</article>
		`);
	}

	return `
		<div class="grid grid-cols-7 gap-2 mb-2">
			${WEEKDAYS.map((day) => `<div class="text-[11px] uppercase tracking-[0.05em] text-center text-on-surface-variant font-semibold">${day}</div>`).join("")}
		</div>
		<div class="space-y-2 overflow-y-auto pr-1" style="height:${CALENDAR_BASE_HEIGHT_PX}px;">
			${dayBlocks.join("")}
		</div>
	`;
}

function buildEventChip(event) {
	const badgeClass = getEventBadgeClass(event.type);
	const course = event.course ? `<div class="text-[10px] text-on-surface-variant mt-1">${escapeHtml(event.course)}</div>` : "";
	const details = [
		event.title || "Untitled",
		formatDateTime(event.startDate),
		event.description || "No description",
	].join("\n");

	return `
		<div class="group relative">
			<div class="${badgeClass} text-[11px] px-2 py-1 rounded-md border truncate" title="${escapeHtml(details)}">
				${escapeHtml(event.title || "Untitled")}
			</div>
			<div class="hidden group-hover:block absolute left-0 top-full mt-1 z-20 w-64 rounded-lg border border-outline-variant/30 bg-surface p-2.5 shadow-xl shadow-black/20">
				<div class="text-xs font-semibold text-on-surface mb-1">${escapeHtml(event.title || "Untitled")}</div>
				<div class="text-[11px] text-on-surface-variant">${escapeHtml(formatDateTime(event.startDate))}</div>
				${course}
				${event.description ? `<div class="text-[11px] text-on-surface-variant mt-1.5 leading-relaxed">${escapeHtml(event.description)}</div>` : ""}
			</div>
		</div>
	`;
}

function renderAssignments() {
	const root = document.getElementById("assignments-root");
	if (!root) {
		return;
	}

	const now = new Date();
	const upcoming = state.events
		.filter((event) => event.startDate >= now)
		.slice(0, 12);

	if (!upcoming.length) {
		root.innerHTML = `
			<div class="md:col-span-2 lg:col-span-3 rounded-xl border border-outline-variant/20 bg-surface-container p-6 text-sm text-on-surface-variant">
				No assignments found yet. Your calendar is still available above.
			</div>
		`;
		return;
	}

	root.innerHTML = upcoming
		.map((event) => {
			const urgency = getUrgencyLabel(event.startDate);
			const accent = getAccent(event.type, event.startDate);

			return `
				<article class="bg-surface-container hover:bg-surface-container-high transition-all duration-300 rounded-xl p-5 relative overflow-hidden flex flex-col gap-3">
					<div class="absolute left-0 top-0 bottom-0 w-1 ${accent.bar}"></div>
					<div class="flex justify-between items-start gap-3">
						<span class="text-[10px] uppercase tracking-[0.05em] font-bold px-2 py-1 rounded ${accent.tag}">${escapeHtml(urgency)}</span>
						<span class="text-[11px] text-on-surface-variant">${escapeHtml(formatDateTime(event.startDate))}</span>
					</div>
					<div>
						<h3 class="text-base font-headline font-semibold text-on-surface leading-tight mb-1">${escapeHtml(event.title || "Untitled")}</h3>
						<p class="text-sm text-on-surface-variant">${escapeHtml(event.course || "Canvas")}</p>
					</div>
					${event.description ? `<p class="text-xs text-on-surface-variant line-clamp-3">${escapeHtml(event.description)}</p>` : ""}
				</article>
			`;
		})
		.join("");
}

function getEventsForDay(date) {
	const dayStart = new Date(date.getFullYear(), date.getMonth(), date.getDate(), 0, 0, 0, 0);
	const dayEnd = new Date(date.getFullYear(), date.getMonth(), date.getDate(), 23, 59, 59, 999);

	return state.events.filter((event) => event.startDate >= dayStart && event.startDate <= dayEnd);
}

function getStartOfWeek(date) {
	const d = new Date(date);
	d.setHours(0, 0, 0, 0);
	d.setDate(d.getDate() - d.getDay());
	return d;
}

function isToday(date) {
	const now = new Date();
	return (
		date.getFullYear() === now.getFullYear() &&
		date.getMonth() === now.getMonth() &&
		date.getDate() === now.getDate()
	);
}

function formatDateTime(date) {
	return date.toLocaleDateString(undefined, {
		month: "short",
		day: "numeric",
		hour: "numeric",
		minute: "2-digit",
	});
}

function getUrgencyLabel(startDate) {
	const now = new Date();
	const diffMs = startDate - now;
	const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

	if (diffDays < 0) {
		return "Past Due";
	}
	if (diffDays === 0) {
		return "Due Today";
	}
	if (diffDays === 1) {
		return "Due Tomorrow";
	}
	if (diffDays < 7) {
		return `Due in ${diffDays} days`;
	}
	return startDate.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function getAccent(type, startDate) {
	const now = new Date();
	const soon = (startDate - now) / (1000 * 60 * 60 * 24) <= 1;

	if (soon) {
		return {
			bar: "bg-error",
			tag: "bg-error-container/20 text-error",
		};
	}

	if (type === "quiz") {
		return {
			bar: "bg-tertiary",
			tag: "bg-tertiary-container/30 text-tertiary",
		};
	}

	return {
		bar: "bg-primary",
		tag: "bg-primary/20 text-primary-fixed",
	};
}

function getEventBadgeClass(type) {
	if (type === "quiz") {
		return "bg-tertiary-container/20 text-tertiary border-tertiary/30";
	}
	if (type === "assignment") {
		return "bg-primary/10 text-primary-fixed border-primary/30";
	}
	return "bg-secondary-container/25 text-on-secondary-container border-secondary-container/30";
}

function escapeHtml(text) {
	return String(text)
		.replaceAll("&", "&amp;")
		.replaceAll("<", "&lt;")
		.replaceAll(">", "&gt;")
		.replaceAll('"', "&quot;")
		.replaceAll("'", "&#39;");
}
