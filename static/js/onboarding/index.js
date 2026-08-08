import { createThemeSelector } from './theme-selector.js';

const onboardingData = JSON.parse(document.getElementById('onboarding-data').textContent);
const onboardingState = {
    step: Number(document.getElementById('active-step')?.value || 1) || 1,
    displayName: onboardingData.displayName,
    username: onboardingData.username,
    educationLevel: onboardingData.educationLevel,
    classYear: onboardingData.classYear,
    emoryStudent: onboardingData.emoryStudent,
    emoryEmail: onboardingData.emoryEmail,
    school: onboardingData.school,
    courses: onboardingData.courses,
    defaultTerm: document.getElementById('default-term').value,
    term: document.getElementById('default-term').value,
    dirty: false,
};
const steps = Array.from(document.querySelectorAll('.wizard-step'));
const form = document.getElementById('onboarding-form');
const progressBar = document.getElementById('progress-bar');
const stepLabel = document.getElementById('step-label');
const stepperStages = Array.from(document.querySelectorAll('[data-onboarding-stage]'));
const welcomeHeader = document.getElementById('onboarding-welcome-header');
const wizardStatus = document.getElementById('wizard-status');
const displayNameInput = document.getElementById('onboarding-display-name');
const usernameInput = document.getElementById('onboarding-username');
const displayNameHelp = document.getElementById('onboarding-display-name-help');
const usernameHelp = document.getElementById('onboarding-username-help');
const courseSearch = document.getElementById('course-search');
const courseSuggestions = document.getElementById('course-suggestions');
const courseCode = document.getElementById('course-code');
const courseName = document.getElementById('course-name');
const sectionNumber = document.getElementById('section-number');
const instructorName = document.getElementById('instructor-name');
const courseList = document.getElementById('course-list');
const courseCount = document.getElementById('course-count');
const termOptions = document.getElementById('term-options');
const addCourseButton = document.getElementById('add-course-button');
const step2Continue = document.getElementById('step-2-continue');
const step3Continue = document.getElementById('step-3-continue');
const coursesStep = document.getElementById('courses-step');
const classYearField = document.getElementById('class-year-field');
const classYearInput = document.getElementById('class-year');
const emoryStudentField = document.getElementById('emory-student-field');
const emoryEmailField = document.getElementById('emory-email-field');
const emoryEmailInput = document.getElementById('emory-email');
const universityField = document.getElementById('university-field');
const universityInput = document.getElementById('university-school');
const universityOptions = document.getElementById('university-options');
const reviewEducationLevel = document.getElementById('review-education-level');
const reviewClassYear = document.getElementById('review-class-year');
const reviewEmoryStudent = document.getElementById('review-emory-student');
const reviewEmoryEmail = document.getElementById('review-emory-email');
const reviewCoursesCard = document.getElementById('review-courses-card');
const reviewBackButton = document.querySelector('button[data-prev="4"]');
const reviewCoursesEditButton = document.querySelector('button[data-go="3"]');
const reviewCourses = document.getElementById('review-courses');
const reviewCoursesEmpty = document.getElementById('review-courses-empty');
const themeSelectorRoot = document.getElementById('onboarding-theme-cards');
let themeSelector = null;
const preferencesStepNumber = document.getElementById('preferences-step-number');
const confirmStepNumber = document.getElementById('confirm-step-number');
const educationLevelGroup = document.getElementById('education-level-group');
const emoryStudentGroup = document.getElementById('emory-student-group');
let onboardingInitialized = false;
const formField = () => window.APStudyFormField;
function showError(message) {
    wizardStatus.textContent = message;
    wizardStatus.classList.remove('hidden');
    wizardStatus.focus({ preventScroll: true });
}
function clearError() {
    wizardStatus.classList.add('hidden');
    wizardStatus.textContent = '';
    formField()?.clearAll?.(form);
}
function markDirty() {
    onboardingState.dirty = true;
}
function clearDirty() {
    onboardingState.dirty = false;
}

function bindHelperText(input, helper) {
    if (!input || !helper) {
        return;
    }
    input.addEventListener('focus', () => helper.classList.remove('hidden'));
    input.addEventListener('blur', () => helper.classList.add('hidden'));
}

function suggestUsername(value) {
    const base = String(value || '')
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9_-]+/g, '-')
        .replace(/^-+|-+$/g, '');
    return base.slice(0, USERNAME_MAX_LENGTH);
}

function validateAccountStep() {
    const displayName = displayNameInput?.value.trim() || '';
    if (!displayName) {
        formField()?.markInvalid?.(displayNameInput);
        showError('Display name is required.');
        return false;
    }
    const rawUsername = usernameInput?.value.trim() || '';
    if (!rawUsername) {
        formField()?.markInvalid?.(usernameInput);
        showError('Username is required.');
        return false;
    }
    const normalizedUsername = rawUsername.toLowerCase();
    if (!USERNAME_PATTERN.test(normalizedUsername)) {
        formField()?.markInvalid?.(usernameInput);
        showError('Please only use numbers, letters, dashes -, or underscores _.');
        return false;
    }
    if (normalizedUsername.length < USERNAME_MIN_LENGTH || normalizedUsername.length > USERNAME_MAX_LENGTH) {
        formField()?.markInvalid?.(usernameInput);
        showError('Username must be between 3 and 20 characters.');
        return false;
    }
    if (USERNAME_RESERVED.has(normalizedUsername)) {
        formField()?.markInvalid?.(usernameInput);
        showError('That username is reserved.');
        return false;
    }
    formField()?.clearInvalid?.([displayNameInput, usernameInput]);
    onboardingState.displayName = displayName;
    onboardingState.username = normalizedUsername;
    if (usernameInput) {
        usernameInput.value = normalizedUsername;
    }
    return true;
}
function fetchJson(url, options = {}) {
    return window.APStudyHttp.fetchJson(url, {
        ...options,
        pendingLabel: options.pendingLabel || 'onboarding-save',
    });
}
const DARK_THEMES = ['obsidian-dark', 'nest-dark'];
const VALID_THEMES = ['obsidian-dark', 'parchment-light', 'system-match', 'nest-light', 'nest-dark'];
const STORAGE_KEY = 'apstudy-theme';
const MAX_OTHER_CALENDARS = 10;
const USERNAME_MIN_LENGTH = 3;
const USERNAME_MAX_LENGTH = 20;
const USERNAME_PATTERN = /^[a-zA-Z0-9_-]+$/;
const USERNAME_RESERVED = new Set([
    'account',
    'admin',
    'api',
    'auth',
    'calendar',
    'dashboard',
    'data',
    'files',
    'login',
    'logout',
    'notes',
    'onboarding',
    'preferences',
    'profile',
    'settings',
    'signup',
    'u',
    'user',
    'users',
]);
const EMORY_SCHOOL_NAME = 'Emory University';
const SEGMENTED_OPTION_CLASSES = 'inline-flex min-h-[48px] w-full items-center justify-center rounded-xl border border-outline-variant/30 bg-surface-container/[0.65] px-4 py-3 text-sm font-medium text-on-surface transition duration-200 ease-out hover:border-outline-variant/50 hover:bg-surface-container-high/90 focus:outline-none focus:ring-1 focus:ring-primary/50 aria-pressed:border-primary/30 aria-pressed:bg-primary/15';
const COURSE_CARD_CLASSES = 'rounded-2xl border border-outline-variant/20 bg-surface-container p-4';
const COURSE_SUGGESTION_CLASSES = 'block w-full appearance-none border-0 bg-transparent px-4 py-3 text-left text-sm text-on-surface transition-colors hover:bg-surface-container-high focus:bg-surface-container-high focus:outline-none';
const REMOVE_CALENDAR_BUTTON_CLASSES = 'btn-remove-calendar inline-flex items-center justify-center rounded-lg h-10 w-10 bg-surface-container-low text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high transition-colors';
let courseSearchTimeout = null;
let universitySearchTimeout = null;
function applyTheme(theme, persist) {
    if (VALID_THEMES.indexOf(theme) === -1) theme = 'obsidian-dark';
    document.documentElement.setAttribute('data-theme', theme);
    if (theme === 'system-match') {
        const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
        document.documentElement.classList.toggle('dark', prefersDark);
    } else {
        document.documentElement.classList.toggle('dark', DARK_THEMES.indexOf(theme) !== -1);
    }
    if (persist) {
        localStorage.setItem(STORAGE_KEY, theme);
    }
    themeSelector?.select(theme);
}
function resolveTheme() {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored && VALID_THEMES.indexOf(stored) !== -1) return stored;
    if (window.matchMedia('(prefers-color-scheme: dark)').matches) return 'obsidian-dark';
    if (window.matchMedia('(prefers-color-scheme: light)').matches) return 'parchment-light';
    return 'obsidian-dark';
}
function updateOtherCalendarCount() {
    const count = document.querySelectorAll('input[data-other-calendar-url]').length;
    const counter = document.getElementById('other-calendar-count');
    if (counter) {
        counter.textContent = `${count} / ${MAX_OTHER_CALENDARS} added`;
    }
}
function updateAddCourseButtonState() {
    if (!addCourseButton) {
        return;
    }
    const ready = Boolean(courseCode.value.trim());
    addCourseButton.disabled = !ready;
    addCourseButton.classList.toggle('opacity-60', !ready);
    addCourseButton.classList.toggle('cursor-not-allowed', !ready);
}
function setActiveTerm(nextTerm) {
    if (!nextTerm) {
        return;
    }
    onboardingState.term = nextTerm;
    updateSegmentedButtons('#term-options button', nextTerm, 'term');
    courseSearch.value = '';
    courseSuggestions.classList.add('hidden');
    courseSuggestions.innerHTML = '';
    courseCode.value = '';
    courseName.value = '';
    sectionNumber.value = '';
    instructorName.value = '';
    updateAddCourseButtonState();
}
function renderTermOptions(terms, selectedTerm) {
    if (!termOptions) {
        return;
    }
    termOptions.innerHTML = '';
    if (!Array.isArray(terms) || terms.length === 0) {
        const fallback = document.createElement('div');
        fallback.className = 'text-sm text-on-surface-variant';
        fallback.textContent = 'No terms available.';
        termOptions.appendChild(fallback);
        return;
    }
    terms.forEach((term) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = SEGMENTED_OPTION_CLASSES;
        button.dataset.term = term;
        button.textContent = term.replace('_', ' ');
        button.addEventListener('click', () => setActiveTerm(term));
        termOptions.appendChild(button);
    });
    updateSegmentedButtons('#term-options button', selectedTerm, 'term');
}
async function loadTerms() {
    try {
        const data = await fetchJson(onboardingData.endpoints.terms);
        const terms = Array.isArray(data.terms) ? data.terms : [];
        const preferred = data.default_term || onboardingState.defaultTerm;
        const selected = terms.includes(onboardingState.term)
            ? onboardingState.term
            : (terms.includes(preferred) ? preferred : terms[0]);
        if (selected) {
            onboardingState.term = selected;
        }
        renderTermOptions(terms, onboardingState.term);
    } catch (error) {
        renderTermOptions([onboardingState.term].filter(Boolean), onboardingState.term);
    }
}
function createOtherCalendarRow(value = '') {
    const row = document.createElement('div');
    row.className = 'other-calendar-row flex gap-2 items-center';
    row.innerHTML = `
        <div class="relative flex-1">
            <input data-other-calendar-url aria-label="Other calendar URL" class="w-full bg-surface-container-lowest border border-outline-variant/30 text-on-surface focus:border-primary/50 focus:outline-none focus:ring-1 focus:ring-primary/50 rounded-lg pl-10 pr-4 py-2.5 transition-all font-body text-sm" placeholder="https://calendar.google.com/..." type="text" value="${value}" />
            <span class="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant text-lg" data-icon="event" aria-hidden="true">event</span>
        </div>
        <button type="button" class="${REMOVE_CALENDAR_BUTTON_CLASSES}" aria-label="Remove calendar link">
            <span class="material-symbols-outlined text-lg" data-icon="close" aria-hidden="true">close</span>
        </button>
    `;
    const input = row.querySelector('input[data-other-calendar-url]');
    formField()?.bindAutoClear?.(input);
    return row;
}
function normalizeCalendarUrlForDedup(url, options = {}) {
    try {
        const trimmed = String(url || '').trim();
        if (!trimmed) {
            return '';
        }
        const allowMissingScheme = options.allowMissingScheme === true;
        const hasScheme = /^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(trimmed);
        const normalizedInput = !hasScheme && allowMissingScheme
            ? `https://${trimmed}`
            : trimmed;
        const parsed = new URL(normalizedInput);
        let scheme = parsed.protocol.replace(':', '').toLowerCase();
        if (scheme === 'webcal') {
            scheme = 'https';
        }
        if (scheme !== 'http' && scheme !== 'https') {
            return '';
        }
        const host = parsed.host.toLowerCase();
        const path = parsed.pathname.replace(/\/+$/, '');
        const query = parsed.search || '';
        return `${scheme}://${host}${path}${query}`;
    } catch (err) {
        return '';
    }
}
function collectOtherCalendarUrls() {
    return Array.from(document.querySelectorAll('input[data-other-calendar-url]'))
        .map(input => input.value.trim())
        .filter(Boolean);
}
function saveStep(step, extra = {}) {
    clearError();
    const payload = { step, ...extra };
    return fetchJson(onboardingData.endpoints.onboarding, {
        method: 'POST',
        body: JSON.stringify(payload),
    }).then((data) => {
        clearDirty();
        return data;
    });
}
function saveInterfaceTheme(theme) {
    const interfaceTheme = VALID_THEMES.includes(theme) ? theme : resolveTheme();
    return fetchJson(onboardingData.endpoints.interfacePreferences, {
        method: 'POST',
        body: JSON.stringify({ interface_theme: interfaceTheme }),
    });
}
function getSelectedEducationLevel() {
    return onboardingState.educationLevel || null;
}
function shouldShowClassYear() {
    return getSelectedEducationLevel() === 'High School' || getSelectedEducationLevel() === 'Undergraduate';
}
function shouldShowEmoryStudentToggle() {
    return getSelectedEducationLevel() === 'Undergraduate';
}
function shouldShowUniversityField() {
    return getSelectedEducationLevel() === 'Undergraduate' && onboardingState.emoryStudent === false;
}
function shouldShowEmoryEmail() {
    return getSelectedEducationLevel() === 'Undergraduate' && onboardingState.emoryStudent === true;
}
function shouldShowCoursesStep() {
    return getSelectedEducationLevel() === 'Undergraduate' && onboardingState.emoryStudent === true;
}
function getVisibleFlow() {
    if (!getSelectedEducationLevel()) {
        return [1, 2, 3, 4, 5];
    }
    return shouldShowCoursesStep() ? [1, 2, 3, 4, 5] : [1, 2, 4, 5];
}
function updateSegmentedButtons(groupSelector, value, attributeName) {
    document.querySelectorAll(groupSelector).forEach((button) => {
        const selected = String(button.dataset[attributeName]) === String(value);
        button.setAttribute('aria-pressed', selected ? 'true' : 'false');
    });
}
function syncEducationVisibility() {
    const educationLevel = getSelectedEducationLevel();
    const classYearVisible = shouldShowClassYear();
    const universityVisible = shouldShowUniversityField();
    const emoryStudentVisible = shouldShowEmoryStudentToggle();
    const emoryEmailVisible = shouldShowEmoryEmail();
    classYearField.classList.toggle('hidden', !classYearVisible);
    universityField.classList.toggle('hidden', !universityVisible);
    emoryStudentField.classList.toggle('hidden', !emoryStudentVisible);
    emoryEmailField.classList.toggle('hidden', !emoryEmailVisible);
    if (!classYearVisible) {
        onboardingState.classYear = '';
        classYearInput.value = '';
    } else {
        classYearInput.value = onboardingState.classYear || '';
    }
    if (!emoryStudentVisible) {
        onboardingState.emoryStudent = null;
        onboardingState.emoryEmail = '';
    }
    if (emoryStudentVisible && onboardingState.emoryStudent === true) {
        onboardingState.school = EMORY_SCHOOL_NAME;
    }
    if (!universityVisible) {
        if (onboardingState.emoryStudent !== true) {
            onboardingState.school = '';
        }
        universityInput.value = '';
    } else {
        universityInput.value = onboardingState.school || '';
    }
    if (!emoryEmailVisible) {
        onboardingState.emoryEmail = '';
        emoryEmailInput.value = '';
    } else {
        emoryEmailInput.value = onboardingState.emoryEmail || '';
    }
    updateSegmentedButtons('#education-level-group button', educationLevel, 'educationLevel');
    updateSegmentedButtons('#emory-student-group button', onboardingState.emoryStudent === null ? '' : onboardingState.emoryStudent ? 'yes' : 'no', 'emoryStudent');
    if (step2Continue) {
        step2Continue.dataset.next = shouldShowCoursesStep() ? '3' : '4';
    }
    const step4Back = document.getElementById('step-4-back');
    if (step4Back) {
        step4Back.dataset.prev = shouldShowCoursesStep() ? '3' : '2';
    }
    if (reviewBackButton) {
        reviewBackButton.dataset.prev = '4';
    }
    if (reviewCoursesEditButton) {
        reviewCoursesEditButton.classList.toggle('hidden', !shouldShowCoursesStep());
    }
    updateCourseContinueButton();
    syncCoursesVisibility();
    updateProgress(onboardingState.step);
}
function syncCoursesVisibility() {
    const coursesVisible = shouldShowCoursesStep();
    coursesStep.classList.toggle('hidden', !coursesVisible);
    reviewCoursesCard.classList.toggle('lg:col-span-2', coursesVisible);
    if (!coursesVisible) {
        reviewCourses.classList.add('hidden');
        reviewCoursesEmpty.classList.remove('hidden');
    } else {
        reviewCourses.classList.remove('hidden');
        reviewCoursesEmpty.classList.add('hidden');
    }
}
function updateCourseContinueButton() {
    if (!step3Continue) {
        return;
    }
    const hasCourses = onboardingState.courses.length > 0;
    step3Continue.innerHTML = hasCourses
        ? 'Continue to review <span class="material-symbols-outlined text-[18px]" aria-hidden="true">arrow_forward</span>'
        : 'Skip / Complete Later <span class="material-symbols-outlined text-[18px]" aria-hidden="true">arrow_forward</span>';
}
function updateStepLabels(flow) {
    const stepMap = new Map();
    flow.forEach((step, index) => {
        stepMap.set(step, index + 1);
    });
    if (preferencesStepNumber) {
        preferencesStepNumber.textContent = `Step ${stepMap.get(4) || 4}`;
    }
    if (confirmStepNumber) {
        confirmStepNumber.textContent = `Step ${stepMap.get(5) || 5}`;
    }
}
function updateProgress(step) {
    const flow = getVisibleFlow();
    const safeStep = flow.includes(step) ? step : flow[flow.length - 1];
    const percent = Math.round((safeStep / 5) * 100);
    progressBar.style.setProperty('--onboarding-progress', String(percent / 100));
    stepLabel.textContent = `Step ${safeStep} of 5`;
    stepperStages.forEach((stage) => {
        const stageNumber = Number(stage.dataset.onboardingStage);
        const skipped = stageNumber === 3 && !flow.includes(3);
        const current = stageNumber === safeStep;
        let state = 'upcoming';
        if (skipped) state = 'skipped';
        else if (stageNumber < safeStep) state = 'completed';
        else if (current) state = 'current';
        stage.dataset.state = state;
        if (current) stage.setAttribute('aria-current', 'step');
        else stage.removeAttribute('aria-current');
    });
    updateStepLabels(flow);
}
function setStep(step) {
    const flow = getVisibleFlow();
    const safeStep = flow.includes(step) ? step : flow[flow.length - 1];
    onboardingState.step = safeStep;
    document.getElementById('active-step').value = safeStep;
    steps.forEach((panel) => {
        const panelStep = Number(panel.dataset.step);
        const isActive = panelStep === safeStep;
        panel.hidden = !isActive;
        if (isActive) {
            panel.classList.remove('hidden');
            panel.classList.add('is-active');
        } else {
            panel.classList.add('hidden');
            panel.classList.remove('is-active');
        }
    });
    if (welcomeHeader) {
        welcomeHeader.classList.toggle('hidden', safeStep !== 1);
    }
    syncEducationVisibility();
    updateProgress(safeStep);
    if (safeStep === 5) {
        renderReview();
    }
    const activeHeading = steps.find((panel) => Number(panel.dataset.step) === safeStep)?.querySelector('h2');
    if (activeHeading) {
        activeHeading.tabIndex = -1;
        if (onboardingInitialized) activeHeading.focus({ preventScroll: false });
    }
}
function validateClassYear() {
    if (!shouldShowClassYear()) {
        return true;
    }
    const value = classYearInput.value.trim();
    if (!/^[0-9]{4}$/.test(value)) {
        formField()?.markInvalid?.(classYearInput);
        showError('Please enter a valid 4-digit class year.');
        return false;
    }
    formField()?.clearInvalid?.(classYearInput);
    return true;
}
function validateEducationStep() {
    if (!getSelectedEducationLevel()) {
        formField()?.markInvalid?.(educationLevelGroup);
        showError('Select an education level before continuing.');
        return false;
    }
    formField()?.clearInvalid?.(educationLevelGroup);
    if (!validateClassYear()) {
        return false;
    }
    if (shouldShowEmoryStudentToggle() && onboardingState.emoryStudent === null) {
        formField()?.markInvalid?.(emoryStudentGroup);
        showError('Please choose whether you are an Emory University student.');
        return false;
    }
    formField()?.clearInvalid?.(emoryStudentGroup);
    if (shouldShowEmoryEmail()) {
        const emailValue = emoryEmailInput.value.trim().toLowerCase();
        if (!emailValue.endsWith('@emory.edu')) {
            formField()?.markInvalid?.(emoryEmailInput);
            showError('Please enter a valid @emory.edu email address.');
            return false;
        }
        formField()?.clearInvalid?.(emoryEmailInput);
    }
    return true;
}
function renderCourseList() {
    courseList.innerHTML = '';
    courseCount.textContent = `${onboardingState.courses.length} added`;
    if (!onboardingState.courses.length) {
        const empty = document.createElement('div');
        empty.className = 'rounded-xl border border-dashed border-outline-variant/25 p-4 text-sm text-on-surface-variant';
        empty.textContent = 'No courses added yet.';
        courseList.appendChild(empty);
        updateCourseContinueButton();
        renderReview();
        return;
    }
    onboardingState.courses.forEach((course, index) => {
        const card = document.createElement('div');
        card.className = COURSE_CARD_CLASSES;
        const details = [course.section_number ? `Section ${course.section_number}` : '', course.instructor_name || ''].filter(Boolean).join(' \u2022 ') || 'No optional details yet';
        card.innerHTML = `
            <div class="flex items-start justify-between gap-4">
                <div>
                    <p class="text-sm font-semibold text-on-surface">${course.course_code || 'Course'}</p>
                    <p class="text-xs text-on-surface-variant mt-1">${course.course_name || 'Course name not set'}</p>
                    <p class="text-xs text-on-surface-variant mt-1">${details}</p>
                </div>
                <button type="button" class="text-sm text-primary hover:underline">Remove</button>
            </div>
        `;
        card.querySelector('button').addEventListener('click', async () => {
            markDirty();
            const [removedCourse] = onboardingState.courses.splice(index, 1);
            clearDirty();
            renderCourseList();
            if (window.APStudyUndo?.stage) {
                window.APStudyUndo.stage({
                    message: `${removedCourse.course_code || 'Course'} removed.`,
                    commit: ({ reason }) => removeCourse(removedCourse.id, { keepalive: reason === 'pagehide' }),
                    restore: () => {
                        onboardingState.courses.splice(Math.min(index, onboardingState.courses.length), 0, removedCourse);
                        renderCourseList();
                    },
                    errorTitle: 'Couldn’t remove course',
                });
                return;
            }
            try {
                await removeCourse(removedCourse.id);
            } catch (error) {
                onboardingState.courses.splice(Math.min(index, onboardingState.courses.length), 0, removedCourse);
                renderCourseList();
                showError(error.message);
            }
        });
        courseList.appendChild(card);
    });
    updateCourseContinueButton();
    renderReview();
}
function renderReview() {
    const educationLevel = getSelectedEducationLevel();
    reviewEducationLevel.textContent = educationLevel ? `Education level: ${educationLevel}` : 'Education level not set.';
    if (shouldShowClassYear() && onboardingState.classYear) {
        reviewClassYear.textContent = `Class year: ${onboardingState.classYear}`;
        reviewClassYear.classList.remove('hidden');
    } else {
        reviewClassYear.classList.add('hidden');
    }
    const reviewSchool = onboardingState.emoryStudent === true ? EMORY_SCHOOL_NAME : onboardingState.school;
    if (getSelectedEducationLevel() === 'Undergraduate' && reviewSchool) {
        reviewClassYear.textContent = reviewClassYear.classList.contains('hidden')
            ? `University: ${reviewSchool}`
            : `${reviewClassYear.textContent} • University: ${reviewSchool}`;
        reviewClassYear.classList.remove('hidden');
    }
    if (educationLevel === 'Undergraduate' && onboardingState.emoryStudent === true) {
        reviewEmoryStudent.textContent = 'Emory student: Yes';
        reviewEmoryStudent.classList.remove('hidden');
    } else {
        reviewEmoryStudent.classList.add('hidden');
    }
    if (shouldShowEmoryEmail() && onboardingState.emoryEmail) {
        reviewEmoryEmail.textContent = `Emory email: ${onboardingState.emoryEmail}`;
        reviewEmoryEmail.classList.remove('hidden');
    } else {
        reviewEmoryEmail.classList.add('hidden');
    }
    if (!shouldShowCoursesStep()) {
        reviewCoursesCard.classList.add('hidden');
        reviewCoursesCard.classList.remove('lg:col-span-2');
        reviewCourses.classList.add('hidden');
        reviewCoursesEmpty.classList.remove('hidden');
        reviewCoursesEmpty.textContent = 'Course entry skipped.';
        return;
    }
    reviewCoursesCard.classList.remove('hidden');
    reviewCoursesCard.classList.add('lg:col-span-2');
    reviewCourses.classList.remove('hidden');
    reviewCoursesEmpty.classList.add('hidden');
    reviewCourses.innerHTML = '';
    if (!onboardingState.courses.length) {
        reviewCourses.innerHTML = '<div class="text-sm text-on-surface-variant">No courses added yet.</div>';
        return;
    }
    onboardingState.courses.forEach((course) => {
        const item = document.createElement('div');
        item.className = COURSE_CARD_CLASSES;
        const details = [course.section_number ? `Section ${course.section_number}` : '', course.instructor_name || ''].filter(Boolean).join(' \u2022 ') || 'No optional details yet';
        item.innerHTML = `
            <div class="flex items-start justify-between gap-4">
                <div>
                    <p class="font-semibold">${course.course_code || 'Course'}</p>
                    <p class="text-sm text-on-surface-variant mt-1">${course.course_name || 'No course name set'}</p>
                    <p class="text-xs text-on-surface-variant mt-1">${details}</p>
                </div>
            </div>
        `;
        reviewCourses.appendChild(item);
    });
}
async function addCourseFromInputs() {
    const courseCodeValue = courseCode.value.trim().toUpperCase();
    const courseNameValue = courseName.value.trim();
    const sectionNumberValue = sectionNumber.value.trim();
    const instructorNameValue = instructorName.value.trim();
    if (!courseCodeValue) {
        formField()?.markInvalid?.(courseCode);
        showError('Enter a course code first.');
        return;
    }
    formField()?.clearInvalid?.(courseCode);
    markDirty();
    const saved = await saveStep(3, {
        action: 'add_course',
        course_code: courseCodeValue,
        course_name: courseNameValue,
        section_number: sectionNumberValue,
        instructor_name: instructorNameValue,
        term: onboardingState.term,
    });
    onboardingState.courses.push({
        id: saved.course.id,
        course_code: saved.course.course_code,
        course_name: saved.course.course_name,
        section_number: saved.course.section_number,
        instructor_name: saved.course.instructor_name,
        term: saved.course.term,
    });
    courseCode.value = '';
    courseName.value = '';
    sectionNumber.value = '';
    instructorName.value = '';
    clearDirty();
    renderCourseList();
    updateAddCourseButtonState();
}
async function removeCourse(courseId, options = {}) {
    const response = await fetch(onboardingData.endpoints.removeCourseTemplate.replace('/0', `/${courseId}`), {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        keepalive: options.keepalive === true,
    });
    const data = await response.json();
    if (!response.ok) {
        throw new Error(data.error || 'Failed to remove course');
    }
}
async function searchCourses(query) {
    const term = query.trim();
    if (term.length < 2) {
        courseSuggestions.classList.add('hidden');
        courseSuggestions.innerHTML = '';
        return;
    }
    try {
        const data = await fetchJson(`${onboardingData.endpoints.courseSearch}?query=${encodeURIComponent(term)}&term=${encodeURIComponent(onboardingState.term)}`,
            { method: 'GET', headers: { 'Content-Type': 'application/json' } });
        courseSuggestions.innerHTML = '';
        const results = data.results || (data.course_code ? [data] : []);
        if (data.course_title) {
            results.unshift(data);
        }
        if (!results.length) {
            courseSuggestions.classList.add('hidden');
            return;
        }
        const maxSuggestions = 8;
        let renderedCount = 0;
        for (const result of results.slice(0, 6)) {
            if (renderedCount >= maxSuggestions) {
                break;
            }
            const label = result.course_code || `${result.subject} ${result.catalog}`;
            const details = result;
            const courseTitle = details.course_title || details.course_name || result.course_title || result.title || 'Course title not available';
            const sections = Array.isArray(details.sections) ? details.sections : [];
            const sectionItems = sections.length ? sections.slice(0, 2) : [null];
            sectionItems.forEach((section) => {
                if (renderedCount >= maxSuggestions) {
                    return;
                }
                const sectionNumberValue = section?.section_number || '';
                const instructorValue = section?.instructor || 'Instructor TBA';
                const timeValue = section?.schedule_display || section?.schedule?.display || 'Time TBA';
                const subtitleParts = [
                    sectionNumberValue ? `Section ${sectionNumberValue}` : '',
                    instructorValue,
                    timeValue,
                ].filter(Boolean);
                const subtitle = subtitleParts.join(' • ');
                const item = document.createElement('button');
                item.type = 'button';
                item.className = COURSE_SUGGESTION_CLASSES;
                item.innerHTML = `
                    <div class="font-medium">${label}</div>
                    <div class="text-xs text-on-surface-variant mt-1">${courseTitle}</div>
                    <div class="text-xs text-on-surface-variant mt-1">${subtitle}</div>
                `;
                item.addEventListener('click', () => {
                    courseCode.value = label;
                    courseName.value = courseTitle;
                    sectionNumber.value = sectionNumberValue;
                    instructorName.value = section?.instructor || '';
                    markDirty();
                    courseSuggestions.classList.add('hidden');
                    updateAddCourseButtonState();
                });
                courseSuggestions.appendChild(item);
                renderedCount += 1;
            });
        }
        courseSuggestions.classList.remove('hidden');
    } catch (error) {
        courseSuggestions.classList.add('hidden');
    }
}
function escapeOptionAttribute(value) {
    return String(value || '')
        .replace(/&/g, '&amp;')
        .replace(/"/g, '&quot;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}
async function searchUniversities(query) {
    const term = String(query || '').trim();
    if (!universityOptions || term.length < 2) {
        return;
    }
    try {
        const data = await fetchJson(`/api/universities?q=${encodeURIComponent(term)}`,
            { method: 'GET', headers: { 'Content-Type': 'application/json' } });
        const results = Array.isArray(data.results) ? data.results : [];
        universityOptions.innerHTML = results.map((school) => {
            const label = [school.name, school.city, school.state].filter(Boolean).join(' - ');
            return `<option value="${escapeOptionAttribute(school.name)}" label="${escapeOptionAttribute(label)}"></option>`;
        }).join('');
    } catch (error) {
        universityOptions.innerHTML = '';
    }
}
function onEducationLevelSelected(level) {
    onboardingState.educationLevel = level;
    formField()?.clearInvalid?.(educationLevelGroup);
    markDirty();
    if (level !== 'Undergraduate') {
        onboardingState.emoryStudent = null;
        onboardingState.emoryEmail = '';
        onboardingState.school = '';
    }
    if (level !== 'High School' && level !== 'Undergraduate') {
        onboardingState.classYear = '';
    }
    syncEducationVisibility();
}
function onEmoryStudentSelected(value) {
    onboardingState.emoryStudent = value === 'yes';
    formField()?.clearInvalid?.(emoryStudentGroup);
    if (value === 'yes') {
        onboardingState.school = EMORY_SCHOOL_NAME;
    } else {
        onboardingState.emoryEmail = '';
        onboardingState.school = '';
    }
    markDirty();
    syncEducationVisibility();
}
function sanitizeClassYearInput() {
    const sanitized = classYearInput.value.replace(/\D/g, '').slice(0, 4);
    if (classYearInput.value !== sanitized) {
        classYearInput.value = sanitized;
    }
    onboardingState.classYear = sanitized;
    markDirty();
}
function syncFieldFromInput(input, key) {
    onboardingState[key] = input.value;
    markDirty();
}
document.querySelectorAll('.btn-next').forEach((button) => {
    button.addEventListener('click', async () => {
        const currentStep = onboardingState.step;
        try {
            if (currentStep === 1) {
                if (!validateAccountStep()) {
                    return;
                }
                await saveStep(1, {
                    display_name: onboardingState.displayName,
                    username: onboardingState.username,
                });
                setStep(2);
                return;
            }
            if (currentStep === 2) {
                if (!validateEducationStep()) {
                    return;
                }
                const payload = {
                    education_level: onboardingState.educationLevel,
                    class_year: shouldShowClassYear() ? onboardingState.classYear : null,
                    school: onboardingState.emoryStudent === true ? EMORY_SCHOOL_NAME : (shouldShowUniversityField() ? onboardingState.school : null),
                    emory_student: shouldShowEmoryStudentToggle() ? onboardingState.emoryStudent : null,
                    emory_email: shouldShowEmoryEmail() ? onboardingState.emoryEmail : null,
                };
                const saved = await saveStep(2, payload);
                const nextStep = Number(saved.next_step || (shouldShowCoursesStep() ? 3 : 4));
                setStep(nextStep);
                return;
            }
            if (currentStep === 3) {
                const saved = await saveStep(3, { action: 'advance' });
                setStep(Number(saved.next_step || 4));
                return;
            }
            if (currentStep === 4) {
                const feedUrl = document.getElementById('canvas-feed-url')?.value || '';
                const otherCalendarUrls = collectOtherCalendarUrls();
                if (otherCalendarUrls.length > MAX_OTHER_CALENDARS) {
                    showError(`You can add up to ${MAX_OTHER_CALENDARS} optional calendar links.`);
                    return;
                }
                const normalizedSeen = new Set();
                const normalizedCanvas = normalizeCalendarUrlForDedup(feedUrl, { allowMissingScheme: true });
                const otherCalendarInputs = Array.from(document.querySelectorAll('input[data-other-calendar-url]'));
                for (const input of otherCalendarInputs) {
                    const url = input.value.trim();
                    if (!url) continue;
                    const normalized = normalizeCalendarUrlForDedup(url);
                    if (!normalized) {
                        formField()?.markInvalid?.(input);
                        showError('Each optional calendar link must be a valid http(s) or webcal URL.');
                        return;
                    }
                    if (normalizedCanvas && normalized === normalizedCanvas) {
                        formField()?.markInvalid?.(input);
                        showError('Optional calendar links cannot duplicate the Nest Canvas calendar.');
                        return;
                    }
                    if (normalizedSeen.has(normalized)) {
                        formField()?.markInvalid?.(input);
                        showError('Duplicate optional calendar links are not allowed.');
                        return;
                    }
                    normalizedSeen.add(normalized);
                }
                formField()?.clearAll?.(form);
                await fetchJson(onboardingData.endpoints.feedUrl, {
                    method: 'POST',
                    body: JSON.stringify({
                        canvas_ical_url: feedUrl,
                        other_ical_urls: otherCalendarUrls,
                    }),
                });
                await saveInterfaceTheme(currentTheme);
                await saveStep(4);
                setStep(5);
                return;
            }
        } catch (error) {
            showError(error.message);
        }
    });
});
document.querySelectorAll('.btn-back').forEach((button) => {
    button.addEventListener('click', () => {
        const prevStep = Number(button.dataset.prev);
        if (onboardingState.step === 4 && !shouldShowCoursesStep() && prevStep === 3) {
            setStep(2);
            return;
        }
        setStep(prevStep);
    });
});
document.querySelectorAll('.edit-step').forEach((button) => {
    button.addEventListener('click', () => setStep(Number(button.dataset.go)));
});
document.querySelectorAll('#education-level-group button[data-education-level]').forEach((button) => {
    button.addEventListener('click', () => onEducationLevelSelected(button.dataset.educationLevel));
});
document.querySelectorAll('#emory-student-group button[data-emory-student]').forEach((button) => {
    button.addEventListener('click', () => onEmoryStudentSelected(button.dataset.emoryStudent));
});
form.addEventListener('input', (event) => {
    if (event.target === displayNameInput) {
        onboardingState.displayName = displayNameInput.value;
        markDirty();
        return;
    }
    if (event.target === usernameInput) {
        onboardingState.username = usernameInput.value;
        markDirty();
        return;
    }
    if (event.target === classYearInput) {
        sanitizeClassYearInput();
        return;
    }
    if (event.target === emoryEmailInput) {
        syncFieldFromInput(emoryEmailInput, 'emoryEmail');
        return;
    }
    if (event.target === universityInput) {
        syncFieldFromInput(universityInput, 'school');
        if (universitySearchTimeout) {
            window.clearTimeout(universitySearchTimeout);
        }
        universitySearchTimeout = window.setTimeout(() => searchUniversities(universityInput.value), 180);
        return;
    }
    if (event.target === courseSearch) {
        if (courseSearchTimeout) {
            window.clearTimeout(courseSearchTimeout);
        }
        courseSearchTimeout = window.setTimeout(() => {
            searchCourses(courseSearch.value);
        }, 250);
        return;
    }
});
form.addEventListener('change', (event) => {
    if (event.target === emoryEmailInput) {
        syncFieldFromInput(emoryEmailInput, 'emoryEmail');
    }
    if (event.target === universityInput) {
        syncFieldFromInput(universityInput, 'school');
    }
});
document.getElementById('add-course-button').addEventListener('click', async () => {
    try {
        await addCourseFromInputs();
    } catch (error) {
        showError(error.message);
    }
});
courseSearch.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
        event.preventDefault();
        searchCourses(courseSearch.value);
    }
});
document.getElementById('finish-button').addEventListener('click', async () => {
    try {
        await saveStep(5);
        clearDirty();
        window.location.href = onboardingData.endpoints.dashboard;
    } catch (error) {
        showError(error.message);
    }
});
window.addEventListener('beforeunload', (event) => {
    if (!onboardingState.dirty) {
        return;
    }
    event.preventDefault();
    event.returnValue = '';
});
document.addEventListener('click', (event) => {
    if (!courseSuggestions.contains(event.target) && event.target !== courseSearch) {
        courseSuggestions.classList.add('hidden');
    }
});
let currentTheme = resolveTheme();
themeSelector = createThemeSelector(themeSelectorRoot, {
    initialTheme: currentTheme,
    onSelect(newTheme) {
        currentTheme = newTheme;
        applyTheme(newTheme, true);
    },
});
applyTheme(currentTheme, false);
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (event) => {
    if (currentTheme === 'system-match') {
        document.documentElement.classList.toggle('dark', event.matches);
    }
});
document.getElementById('add-other-calendar')?.addEventListener('click', () => {
    const container = document.getElementById('other-calendar-links');
    if (!container) {
        return;
    }
    if (container.querySelectorAll('input[data-other-calendar-url]').length >= MAX_OTHER_CALENDARS) {
        showError(`You can add up to ${MAX_OTHER_CALENDARS} optional calendar links.`);
        return;
    }
    container.appendChild(createOtherCalendarRow());
    updateOtherCalendarCount();
});
document.getElementById('other-calendar-links')?.addEventListener('click', (event) => {
    const button = event.target.closest('.btn-remove-calendar');
    if (!button) {
        return;
    }
    const row = button.closest('.other-calendar-row');
    if (row) {
        const container = row.parentElement;
        const rowIndex = Array.from(container?.children || []).indexOf(row);
        const removedUrl = row.querySelector('input[data-other-calendar-url]')?.value || '';
        row.remove();
        updateOtherCalendarCount();
        window.APStudyUndo?.stage?.({
            title: 'Calendar link removed',
            message: 'Save your setup to apply this change.',
            restore: () => {
                if (!container) return;
                const restored = createOtherCalendarRow(removedUrl);
                const reference = container.children[rowIndex] || null;
                container.insertBefore(restored, reference);
                updateOtherCalendarCount();
            },
        });
    }
});
updateOtherCalendarCount();
function initializeOnboarding() {
    if (onboardingState.step === 3 && !shouldShowCoursesStep()) {
        onboardingState.step = 4;
    }
    if (displayNameInput && onboardingState.displayName) {
        displayNameInput.value = onboardingState.displayName;
    }
    if (usernameInput) {
        if (onboardingState.username) {
            usernameInput.value = onboardingState.username;
        } else if (displayNameInput?.value) {
            const suggestion = suggestUsername(displayNameInput.value);
            usernameInput.value = suggestion;
            onboardingState.username = suggestion;
        }
    }
    if (onboardingState.classYear) {
        classYearInput.value = onboardingState.classYear;
    }
    if (onboardingState.emoryEmail) {
        emoryEmailInput.value = onboardingState.emoryEmail;
    }
    if (onboardingState.school) {
        universityInput.value = onboardingState.school;
    }
    bindHelperText(displayNameInput, displayNameHelp);
    bindHelperText(usernameInput, usernameHelp);
    formField()?.bindAutoClear?.([
        displayNameInput,
        usernameInput,
        classYearInput,
        emoryEmailInput,
        courseCode,
        document.getElementById('canvas-feed-url'),
    ]);
    document.querySelectorAll('input[data-other-calendar-url]').forEach((input) => {
        formField()?.bindAutoClear?.(input);
    });
    syncEducationVisibility();
    renderCourseList();
    updateAddCourseButtonState();
    loadTerms();
    setStep(onboardingState.step);
    onboardingInitialized = true;
    clearDirty();
}
initializeOnboarding();
