(() => {
    const { escapeHtml } = window.APStudyUIPrimitives;
    const nav = document.querySelector("[data-landing-nav]");
    const navToggle = document.querySelector("[data-landing-nav-toggle]");
    const navMenu = document.querySelector("[data-landing-nav-menu]");
    const hero = document.querySelector(".landing-hero");
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    if (navToggle && navMenu) {
        const closeMenu = ({ restoreFocus = false } = {}) => {
            navMenu.classList.remove("is-open");
            navToggle.setAttribute("aria-expanded", "false");
            navToggle.setAttribute("aria-label", "Open navigation");
            if (restoreFocus) navToggle.focus({ preventScroll: true });
        };

        navToggle.addEventListener("click", () => {
            const open = navMenu.classList.toggle("is-open");
            navToggle.setAttribute("aria-expanded", String(open));
            navToggle.setAttribute("aria-label", open ? "Close navigation" : "Open navigation");
        });

        navMenu.addEventListener("click", (event) => {
            if (!(event.target instanceof HTMLAnchorElement)) return;
            closeMenu();
        });

        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && navMenu.classList.contains("is-open")) {
                closeMenu({ restoreFocus: true });
            }
        });
    }

    const syncNav = () => {
        nav?.classList.toggle("is-scrolled", window.scrollY > 12);
        if (hero && !reducedMotion) {
            const progress = Math.min(window.scrollY / Math.max(hero.offsetHeight, 1), 1);
            hero.style.setProperty("--hero-parallax", String(progress));
        }
    };

    syncNav();
    window.addEventListener("scroll", syncNav, { passive: true });
})();

(() => {
    const root = document.querySelector("[data-landing-rotator]");
    const track = root?.querySelector("[data-landing-rotator-track]");
    if (!root || !track || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    const outcomes = Array.from(track.children);
    if (outcomes.length < 2) return;

    let index = 0;
    let timer = null;
    const stop = () => {
        if (timer !== null) window.clearInterval(timer);
        timer = null;
    };
    const start = () => {
        if (timer !== null || document.hidden) return;
        timer = window.setInterval(() => {
            index = (index + 1) % outcomes.length;
            track.style.setProperty("--landing-rotator-index", String(index));
        }, 2000);
    };

    root.addEventListener("mouseenter", stop);
    root.addEventListener("mouseleave", start);
    root.addEventListener("focusin", stop);
    root.addEventListener("focusout", start);
    document.addEventListener("visibilitychange", () => (document.hidden ? stop() : start()));
    start();
})();

(() => {
    const root = document.querySelector("[data-landing-faq]");
    if (!root) return;

    const triggers = Array.from(root.querySelectorAll("[data-landing-faq-trigger]"));
    const setOpen = (trigger, open) => {
        const panelId = trigger.getAttribute("aria-controls");
        const panel = panelId ? document.getElementById(panelId) : null;
        const item = trigger.closest(".landing-faq-item");
        if (!panel || !item) return;
        trigger.setAttribute("aria-expanded", String(open));
        panel.hidden = !open;
        item.classList.toggle("is-open", open);
    };

    triggers.forEach((trigger, triggerIndex) => {
        trigger.addEventListener("click", () => {
            const willOpen = trigger.getAttribute("aria-expanded") !== "true";
            triggers.forEach((candidate) => setOpen(candidate, candidate === trigger && willOpen));
        });

        trigger.addEventListener("keydown", (event) => {
            let nextIndex = triggerIndex;
            if (event.key === "ArrowDown") nextIndex = (triggerIndex + 1) % triggers.length;
            else if (event.key === "ArrowUp") nextIndex = (triggerIndex - 1 + triggers.length) % triggers.length;
            else if (event.key === "Home") nextIndex = 0;
            else if (event.key === "End") nextIndex = triggers.length - 1;
            else return;
            event.preventDefault();
            triggers[nextIndex].focus({ preventScroll: true });
        });
    });
})();

(() => {
    document.querySelectorAll("[data-landing-cta]").forEach((link) => {
        link.addEventListener("click", () => {
            if (typeof window.gtag !== "function") return;
            window.gtag("event", "landing_cta_click", {
                placement: link.dataset.landingCta || "unknown",
                destination: link.dataset.landingDestination || "unknown",
            });
        });
    });
})();

(() => {
    const tabsRoot = document.querySelector("[data-landing-tabs]");
    if (!tabsRoot) return;

    const tabs = Array.from(tabsRoot.querySelectorAll("[data-landing-tab]"));
    const panels = Array.from(document.querySelectorAll("[data-landing-panel]"));

    function activateTab(nextTab, shouldFocus = false) {
        const target = nextTab?.dataset?.landingTab;
        if (!target) return;

        tabs.forEach((tab) => {
            const selected = tab === nextTab;
            tab.classList.toggle("is-active", selected);
            tab.setAttribute("aria-selected", String(selected));
            tab.tabIndex = selected ? 0 : -1;
        });

        panels.forEach((panel) => {
            const selected = panel.dataset.landingPanel === target;
            panel.classList.toggle("is-active", selected);
            panel.hidden = !selected;
        });

        if (shouldFocus) nextTab.focus({ preventScroll: true });
    }

    tabs.forEach((tab) => {
        tab.addEventListener("click", () => activateTab(tab));
        tab.addEventListener("keydown", (event) => {
            const currentIndex = tabs.indexOf(tab);
            let nextIndex = currentIndex;
            if (event.key === "ArrowRight" || event.key === "ArrowDown") {
                nextIndex = (currentIndex + 1) % tabs.length;
            } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
                nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
            } else if (event.key === "Home") {
                nextIndex = 0;
            } else if (event.key === "End") {
                nextIndex = tabs.length - 1;
            } else {
                return;
            }

            event.preventDefault();
            activateTab(tabs[nextIndex], true);
        });
    });
})();

(() => {
    const weekGrid = document.querySelector("[data-dashboard-week-grid]");
    if (!weekGrid) return;

    const dateCells = Array.from(weekGrid.querySelectorAll("[data-week-date]"));
    if (dateCells.length !== 7) return;

    const today = new Date();
    const weekStart = new Date(today);
    weekStart.setHours(0, 0, 0, 0);
    weekStart.setDate(today.getDate() - today.getDay());

    dateCells.forEach((cell, index) => {
        const cellDate = new Date(weekStart);
        cellDate.setDate(weekStart.getDate() + index);
        cell.textContent = String(cellDate.getDate());
        cell.classList.toggle("is-current", index === today.getDay());
        cell.setAttribute("aria-label", cellDate.toLocaleDateString(undefined, {
            weekday: "long",
            month: "long",
            day: "numeric",
        }));
    });

    const events = Array.from(weekGrid.querySelectorAll("i"));
    const dayClasses = ["day-monday", "day-tuesday", "day-wednesday", "day-thursday", "day-friday"];
    const colorClasses = ["event-blue", "event-orange", "event-red"];
    const slotClasses = ["event-slot-2", "event-slot-3"];
    const denseDayIndex = today.getDate() % dayClasses.length;
    const doubleDayIndex = (denseDayIndex + 2) % dayClasses.length;
    const eventPlan = dayClasses.flatMap((dayClass, dayIndex) => {
        const count = dayIndex === denseDayIndex ? 3 : dayIndex === doubleDayIndex ? 2 : 1;
        return Array.from({ length: count }, (_, slotIndex) => ({ dayClass, slotIndex }));
    });

    events.forEach((event, index) => {
        const plan = eventPlan[index];
        if (!plan) {
            event.hidden = true;
            return;
        }

        event.hidden = false;
        event.classList.remove(...dayClasses, ...colorClasses, ...slotClasses);
        event.classList.add(plan.dayClass, colorClasses[(index + today.getDay()) % colorClasses.length]);
        if (plan.slotIndex > 0) event.classList.add(slotClasses[plan.slotIndex - 1]);
    });
})();

(() => {
    const title = document.querySelector("[data-landing-calendar-title]");
    const daysRoot = document.querySelector("[data-landing-calendar-days]");
    const eventsRoot = document.querySelector("[data-landing-calendar-events]");
    const agendaRoot = document.querySelector("[data-landing-calendar-agenda]");
    if (!title || !daysRoot || !eventsRoot || !agendaRoot) return;

    const weekdayLabels = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    const weekdayClassNames = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"];
    const dayHeaders = Array.from(daysRoot.querySelectorAll("span")).slice(1);
    const startHour = 8;
    const endHour = 17;
    const totalMinutes = (endHour - startHour) * 60;
    const showcaseEvents = [
        { day: 1, title: "JPN 101", time: "10:00AM-11:15AM", start: "10:00", end: "11:15", tone: "gold" },
        { day: 1, title: "HLTH 100", time: "11:30AM-12:20PM", start: "11:30", end: "12:20", tone: "purple" },
        { day: 1, title: "BIOL 141", time: "2:30PM-3:45PM", start: "14:30", end: "15:45", tone: "red" },
        { day: 2, title: "CHEM 150", time: "11:30AM-12:45PM", start: "11:30", end: "12:45", tone: "teal" },
        { day: 2, title: "BIOL 141L", time: "2:30PM-5:00PM", start: "14:30", end: "17:00", tone: "green" },
        { day: 3, title: "Work", time: "8:30AM-10:00AM", start: "08:30", end: "10:00", tone: "blue" },
        { day: 3, title: "JPN 101", time: "10:00AM-11:15AM", start: "10:00", end: "11:15", tone: "gold" },
        { day: 3, title: "BIOL 141", time: "2:30PM-3:45PM", start: "14:30", end: "15:45", tone: "red" },
        { day: 4, title: "CHEM 150", time: "11:30AM-12:45PM", start: "11:30", end: "12:45", tone: "teal" },
        { day: 4, title: "CHEM 150L", time: "4:30PM-5:00PM", start: "16:30", end: "17:00", tone: "indigo" },
        { day: 5, title: "JPN 101", time: "10:00AM-10:50AM", start: "10:00", end: "10:50", tone: "gold" },
        { day: 5, title: "ECS 101", time: "11:00AM-11:50AM", start: "11:00", end: "11:50", tone: "cyan" },
        { day: 6, title: "Hangout", time: "3:00PM-4:30PM", start: "15:00", end: "16:30", tone: "pink" },
    ];

    function minutesFromMidnight(time) {
        const [hour, minute] = time.split(":").map(Number);
        return hour * 60 + minute;
    }

    function monthDay(date) {
        return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
    }

    const today = new Date();
    const weekStart = new Date(today);
    weekStart.setHours(0, 0, 0, 0);
    weekStart.setDate(today.getDate() - today.getDay());
    const weekEnd = new Date(weekStart);
    weekEnd.setDate(weekStart.getDate() + 6);

    title.textContent = `Week of ${monthDay(weekStart)} - ${monthDay(weekEnd)}`;

    dayHeaders.forEach((header, index) => {
        const date = new Date(weekStart);
        date.setDate(weekStart.getDate() + index);
        const isToday = date.toDateString() === today.toDateString();
        header.classList.toggle("is-today", isToday);
        header.setAttribute("aria-label", date.toLocaleDateString(undefined, {
            weekday: "long",
            month: "long",
            day: "numeric",
        }));
        header.innerHTML = `<b>${weekdayLabels[index]}</b><em>${date.getDate()}</em>`;
    });

    const dayDividers = Array.from(eventsRoot.querySelectorAll(":scope > i")).map((divider) => divider.outerHTML).join("");
    eventsRoot.innerHTML = `${dayDividers}${showcaseEvents.map((event) => {
        const startMinutes = Math.max(0, minutesFromMidnight(event.start) - startHour * 60);
        const endMinutes = Math.min(totalMinutes, minutesFromMidnight(event.end) - startHour * 60);
        const durationMinutes = Math.max(24, endMinutes - startMinutes);
        const dayClass = `demo-day-${weekdayClassNames[event.day]}`;
        const startClass = `demo-start-${startMinutes}`;
        const durationClass = `demo-duration-${durationMinutes}`;
        return `
            <article class="demo-week-event demo-event-${event.tone} ${dayClass} ${startClass} ${durationClass}">
                <strong>${escapeHtml(event.title)}</strong>
                <span>${escapeHtml(event.time)}</span>
            </article>
        `;
    }).join("")}`;

    agendaRoot.innerHTML = showcaseEvents.map((event) => `
        <p><strong>${weekdayLabels[event.day]}</strong><span>${escapeHtml(event.title)}, ${escapeHtml(event.time)}</span></p>
    `).join("");
})();
