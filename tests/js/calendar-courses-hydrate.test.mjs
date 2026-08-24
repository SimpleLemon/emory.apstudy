import assert from "node:assert/strict";
import { cp, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const simulatedCalendarName = "Simulated Courses";
const storageKey = "coursesSelectedSectionIds";
const savedSection = {
    section_id: "2026_spring_CS_170_1",
    course_code: "CS 170",
    course_title: "Introduction to Computer Science",
    section_number: "1",
    instructor: "Ada Lovelace",
    meetings: [{ day: "Mon", start: "1000", end: "1115" }],
    date_range: { start: "2026-01-12", end: "2026-01-18" },
};

function createMemoryStorage(initial = {}) {
    const memory = new Map(Object.entries(initial).map(([key, value]) => [key, String(value)]));
    return {
        getItem(key) {
            return memory.has(key) ? memory.get(key) : null;
        },
        setItem(key, value) {
            memory.set(key, String(value));
        },
        removeItem(key) {
            memory.delete(key);
        },
    };
}

function jsonResponse(payload, status = 200) {
    return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => payload,
    };
}

const courseModalStub = {
    createCourseModalRenderer() {
        return {
            renderCoursesModal() {},
            setCoursesModalBackgroundInert() {},
        };
    },
};

let coursesFactory = null;

async function loadCoursesModule() {
    if (coursesFactory) {
        globalThis.window.APStudyCalendarCourseModal = courseModalStub;
        return { restore() {} };
    }
    const view = {
        localStorage: createMemoryStorage(),
        location: { href: "https://example.test/calendar" },
        history: { replaceState() {} },
        setTimeout,
        addEventListener() {},
        APStudyCalendarCourseModal: courseModalStub,
    };
    const document = {
        body: { dataset: {} },
        defaultView: view,
        addEventListener() {},
        querySelector: () => null,
    };
    view.document = document;
    view.window = view;
    globalThis.window = view;
    globalThis.document = document;
    const source = await readFile(path.join(repoRoot, "static/js/calendar/integrations/courses.js"), "utf8");
    await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}#courses.js`);
    coursesFactory = view.APStudyCalendarCourses.createCalendarCourses;
    return { restore() {} };
}

function createCoursesRuntime({ emory = true, storage, dataAdapter } = {}) {
    const memory = storage || createMemoryStorage();
    const view = {
        localStorage: memory,
        location: { href: "https://example.test/calendar" },
        history: { replaceState() {} },
        setTimeout,
        APStudyCalendarCourseModal: courseModalStub,
    };
    const document = {
        body: { dataset: { emoryStudent: emory ? "true" : "false" } },
        defaultView: view,
        activeElement: null,
        querySelector: () => null,
    };
    view.document = document;
    const root = {
        ownerDocument: document,
        dataset: { emoryStudent: emory ? "true" : "false" },
        classList: { add() {}, remove() {} },
        querySelector: () => null,
    };
    const state = {
        public: { readOnly: false },
        calendars: {},
        calendarColors: ["#0ea5e9", "#f97316"],
        courses: {
            terms: [],
            sections: [],
            sectionsById: {},
            indexLoaded: false,
            selectedSectionIds: new Set(),
            pinnedSectionIds: new Set(),
            modalOpen: false,
            modalTriggerEl: null,
            isClosing: false,
            animateOnOpen: false,
            showSelectedOnly: false,
            loading: false,
            error: "",
            searchQuery: "",
            searchInput: "",
            termFilter: "",
            filteredSectionIds: [],
        },
    };
    globalThis.window.APStudyCalendarCourseModal = courseModalStub;
    const courses = coursesFactory({
        root,
        lifecycle: {
            trackAbortController() {
                return new AbortController();
            },
            releaseAbortController() {},
        },
        dataAdapter,
        state,
        constants: {
            coursesSelectionStorageKey: storageKey,
            coursesModalAnimationMs: 0,
            simulatedCalendarName,
        },
        render() {},
        saveCalendarState() {},
        escapeHtml: (value) => String(value ?? ""),
    });
    return { courses, memory, state };
}

test("Emory students with saved courses hydrate Simulated Courses meetings", async () => {
    const runtime = await loadCoursesModule();
    try {
        const fetches = [];
        const { courses, memory, state } = createCoursesRuntime({
            emory: true,
            storage: createMemoryStorage({ [storageKey]: JSON.stringify(["stale-id"]) }),
            dataAdapter: {
                async loadSavedCourses() {
                    fetches.push("/api/courses/saved");
                    return {
                        response: jsonResponse({ courses: [savedSection] }),
                        payload: { courses: [savedSection] },
                    };
                },
            },
        });

        courses.initializeCourseSelectionsFromStorage();
        await courses.hydrateSavedCourses();

        assert.deepEqual(fetches, ["/api/courses/saved"]);
        assert.deepEqual([...state.courses.selectedSectionIds], [savedSection.section_id]);
        assert.equal(Boolean(state.calendars[simulatedCalendarName]), true);
        assert.equal(state.courses.sectionsById[savedSection.section_id].course_code, "CS 170");
        assert.equal(JSON.parse(memory.getItem(storageKey))[0], savedSection.section_id);

        const events = courses.buildSimulatedMeetingEvents(
            new Date(2026, 0, 12),
            new Date(2026, 0, 12),
        );
        assert.equal(events.length, 1);
        assert.equal(events[0].title, "CS 170");
        assert.equal(events[0].source, "simulated");
        assert.equal(events[0].startDate.getHours(), 10);
        assert.equal(events[0].endDate.getMinutes(), 15);
    } finally {
        runtime.restore();
    }
});

test("Emory students with an empty saved list hide Simulated Courses", async () => {
    const runtime = await loadCoursesModule();
    try {
        const { courses, memory, state } = createCoursesRuntime({
            emory: true,
            storage: createMemoryStorage({ [storageKey]: JSON.stringify(["stale-id"]) }),
            dataAdapter: {
                async loadSavedCourses() {
                    return {
                        response: jsonResponse({ courses: [] }),
                        payload: { courses: [] },
                    };
                },
            },
        });

        courses.initializeCourseSelectionsFromStorage();
        await courses.hydrateSavedCourses();

        assert.equal(state.courses.selectedSectionIds.size, 0);
        assert.equal(state.calendars[simulatedCalendarName], undefined);
        assert.deepEqual(JSON.parse(memory.getItem(storageKey) || "[]"), []);
        assert.equal(courses.buildSimulatedMeetingEvents(new Date(2026, 0, 12), new Date(2026, 0, 18)).length, 0);
    } finally {
        runtime.restore();
    }
});

test("a 403 saved-courses response hides Simulated Courses", async () => {
    const runtime = await loadCoursesModule();
    try {
        const { courses, memory, state } = createCoursesRuntime({
            emory: true,
            storage: createMemoryStorage({ [storageKey]: JSON.stringify(["stale-id"]) }),
            dataAdapter: {
                async loadSavedCourses() {
                    return {
                        response: jsonResponse({ error: "forbidden" }, 403),
                        payload: { error: "forbidden" },
                    };
                },
            },
        });

        courses.initializeCourseSelectionsFromStorage();
        await courses.hydrateSavedCourses();

        assert.equal(state.courses.selectedSectionIds.size, 0);
        assert.equal(state.calendars[simulatedCalendarName], undefined);
        assert.deepEqual(JSON.parse(memory.getItem(storageKey) || "[]"), []);
    } finally {
        runtime.restore();
    }
});

test("non-Emory sessions skip saved-course fetch and hide Simulated Courses", async () => {
    const runtime = await loadCoursesModule();
    try {
        let fetched = false;
        const { courses, memory, state } = createCoursesRuntime({
            emory: false,
            storage: createMemoryStorage({ [storageKey]: JSON.stringify([savedSection.section_id]) }),
            dataAdapter: {
                async loadSavedCourses() {
                    fetched = true;
                    return { response: jsonResponse({ courses: [savedSection] }, 403) };
                },
            },
        });

        courses.initializeCourseSelectionsFromStorage();
        await courses.hydrateSavedCourses();

        assert.equal(fetched, false);
        assert.equal(state.courses.selectedSectionIds.size, 0);
        assert.equal(state.calendars[simulatedCalendarName], undefined);
        assert.deepEqual(JSON.parse(memory.getItem(storageKey) || "[]"), []);
    } finally {
        runtime.restore();
    }
});

test("calendar data adapter loads saved courses from the Nest API", async () => {
    const moduleRoot = await mkdtemp(path.join(os.tmpdir(), "apstudy-calendar-saved-courses-"));
    await writeFile(path.join(moduleRoot, "package.json"), '{"type":"module"}\n');
    await cp(path.join(repoRoot, "static/js/calendar/capabilities.js"), path.join(moduleRoot, "capabilities.js"));
    await cp(path.join(repoRoot, "static/js/calendar/adapter.js"), path.join(moduleRoot, "adapter.js"));
    try {
        const adapterModule = await import(pathToFileURL(path.join(moduleRoot, "adapter.js")).href);
        const urls = [];
        const adapter = adapterModule.createCalendarDataAdapter({
            fetch: async (url) => {
                urls.push(url);
                return jsonResponse({ courses: [] });
            },
        });
        const result = await adapter.loadSavedCourses();
        assert.deepEqual(urls, ["/api/courses/saved"]);
        assert.equal(result.response.ok, true);
        assert.deepEqual(result.payload, { courses: [] });
    } finally {
        await rm(moduleRoot, { recursive: true, force: true });
    }
});
