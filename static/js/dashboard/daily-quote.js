(function () {
    "use strict";

    const SLOT_ID = "dashboard-daily-quote";
    const STYLE_ID = "dashboard-daily-quote-style";
    const FETCH_TIMEOUT_MS = 8000;
    const {
        fetchQuote,
        getDailyKey,
        isVisible,
        readCache,
        setVisible,
        writeCache,
    } = window.APStudyDashboardDailyQuoteData;

    /*
     * Animation timing and easing controls.
     * WOBBLE_DEGREES mirrors the CSS +/- tilt value used in @keyframes dashboardEggWobble.
     * EGG_ELASTIC_EASING is injected as a CSS variable so the drop can be tuned in one place.
     */
    const WOBBLE_DEGREES = 15;
    const EGG_ELASTIC_EASING = "cubic-bezier(0.175, 0.885, 0.32, 1.275)";
    const PHASE_FRACTURE_MS = 800;
    const PHASE_SPLIT_MS = 1200;
    const PHASE_REVEAL_GATE_MS = 2000;

    /*
     * Particle physics controls.
     * Velocities are pixels per millisecond; gravity is pixels per millisecond squared.
     * Increase PARTICLE_BURST_SPEED for a sharper crack, or PARTICLE_GRAVITY for a heavier falloff.
     */
    const PARTICLE_COUNT = 8;
    const PARTICLE_BURST_SPEED = 0.28;
    const PARTICLE_GRAVITY = 0.0018;
    const PARTICLE_DRAG = 0.992;
    const PARTICLE_TTL_MS = 760;

    const css = `
        :root{--dashboard-egg-elastic:${EGG_ELASTIC_EASING};--dashboard-egg-wobble:${WOBBLE_DEGREES}deg}
        .dashboard-daily-quote-slot:empty{display:none}
        .dashboard-daily-quote-slot{position:relative;isolation:isolate}
        .dashboard-egg-experience{position:relative;display:grid;box-sizing:border-box;place-items:center;width:min(920px,100%);min-height:clamp(142px,15vw,176px);margin:0 auto;overflow:hidden;border:1px solid color-mix(in srgb,var(--color-primary) 24%,var(--color-outline-variant));border-radius:8px;background:radial-gradient(circle at 50% 44%,color-mix(in srgb,var(--color-primary) 12%,transparent),transparent 30%),linear-gradient(135deg,color-mix(in srgb,var(--color-surface-container-low) 92%,var(--color-background)),var(--color-surface));color:var(--color-on-surface);box-shadow:0 18px 46px rgba(0,0,0,0.13);padding:clamp(16px,2vw,22px);text-align:center;transition:opacity 420ms ease,transform 420ms ease,filter 420ms ease}
        .dashboard-egg-experience::before{content:"";position:absolute;inset:0;background:linear-gradient(90deg,transparent,rgba(255,255,255,0.05),transparent);pointer-events:none}
        .dashboard-egg-experience.is-complete{min-height:86px;overflow:visible}
        .dashboard-egg-stage{position:absolute;inset:0;z-index:1;display:grid;gap:10px;place-items:center;align-content:center;pointer-events:none;transition:opacity 360ms ease,transform 420ms cubic-bezier(0.16,1,0.3,1),filter 420ms ease;will-change:opacity,transform,filter}
        .dashboard-egg-drop{position:relative;width:clamp(54px,7vw,76px);height:clamp(72px,9.4vw,102px);transform-origin:50% 72%;animation:dashboardEggDrop 800ms var(--dashboard-egg-elastic) both;will-change:transform}
        .dashboard-egg-figure{position:relative;width:100%;height:100%;transform-origin:50% 72%;animation:dashboardEggWobble 92ms ease-in-out 8 120ms alternate;will-change:transform}
        .dashboard-egg-shell,.dashboard-egg-half{position:absolute;inset:0;border-radius:54% 54% 46% 46% / 62% 62% 40% 40%;background:radial-gradient(circle at 40% 23%,rgba(255,255,255,0.96),rgba(255,250,228,0.75) 20%,transparent 40%),linear-gradient(150deg,#fff9df 0%,#f2dfaa 52%,#d7ad68 100%);box-shadow:inset -10px -14px 26px rgba(114,73,28,0.22),inset 9px 8px 18px rgba(255,255,255,0.56),0 16px 28px rgba(0,0,0,0.24);transition:transform 760ms cubic-bezier(0.2,0.78,0.25,1),opacity 620ms ease;will-change:transform,opacity}
        .dashboard-egg-half{opacity:0}
        .dashboard-egg-half-left{clip-path:polygon(0 0,53% 0,48% 31%,55% 39%,46% 48%,53% 58%,45% 68%,52% 100%,0 100%)}
        .dashboard-egg-half-right{clip-path:polygon(48% 0,100% 0,100% 100%,48% 100%,55% 68%,47% 58%,56% 48%,49% 39%,57% 31%)}
        .dashboard-egg-aura,.dashboard-egg-core{position:absolute;left:50%;top:50%;border-radius:999px;transform:translate3d(-50%,-50%,0) scale3d(0.35,0.35,1);opacity:0;pointer-events:none;will-change:transform,opacity}
        .dashboard-egg-aura{width:clamp(110px,16vw,180px);height:clamp(110px,16vw,180px);background:radial-gradient(circle,rgba(255,242,170,0.72) 0 14%,rgba(92,211,202,0.26) 32%,transparent 68%);filter:blur(2px);transition:transform 760ms cubic-bezier(0.16,1,0.3,1),opacity 560ms ease}
        .dashboard-egg-core{width:clamp(46px,6vw,70px);height:clamp(46px,6vw,70px);background:radial-gradient(circle,#fff7ba 0 18%,rgba(255,215,106,0.72) 38%,rgba(67,214,202,0.24) 70%,transparent 72%);box-shadow:0 0 24px rgba(255,232,137,0.76),0 0 58px rgba(61,212,201,0.36)}
        .dashboard-egg-particles{position:absolute;left:50%;top:50%;width:1px;height:1px;overflow:visible;pointer-events:none}
        .dashboard-egg-particle{position:absolute;width:9px;height:7px;border-radius:70% 35% 60% 40%;background:linear-gradient(135deg,#fff6ce,#d9ad63);box-shadow:0 0 10px rgba(255,228,143,0.5);will-change:transform,opacity}
        .dashboard-egg-loading-text{position:relative;z-index:1;color:var(--color-on-surface-variant);font-size:13px;font-weight:720;letter-spacing:0}
        .dashboard-egg-experience[data-phase="fracture"] .dashboard-egg-shell,.dashboard-egg-experience[data-phase="split"] .dashboard-egg-shell,.dashboard-egg-experience[data-phase="hold"] .dashboard-egg-shell{opacity:0}
        .dashboard-egg-experience[data-phase="fracture"] .dashboard-egg-half{opacity:1}
        .dashboard-egg-experience[data-phase="split"] .dashboard-egg-half-left,.dashboard-egg-experience[data-phase="hold"] .dashboard-egg-half-left{opacity:0;transform:translate3d(-28px,-5px,0) rotate3d(0,0,1,-18deg) scale3d(0.88,0.88,1)}
        .dashboard-egg-experience[data-phase="split"] .dashboard-egg-half-right,.dashboard-egg-experience[data-phase="hold"] .dashboard-egg-half-right{opacity:0;transform:translate3d(28px,3px,0) rotate3d(0,0,1,18deg) scale3d(0.88,0.88,1)}
        .dashboard-egg-experience[data-phase="split"] .dashboard-egg-aura,.dashboard-egg-experience[data-phase="hold"] .dashboard-egg-aura{opacity:1;transform:translate3d(-50%,-50%,0) scale3d(1,1,1)}
        .dashboard-egg-experience[data-phase="hold"] .dashboard-egg-core{opacity:1;animation:dashboardEggCorePulse 1250ms ease-in-out infinite}
        .dashboard-egg-experience[data-phase="quote"]{overflow:visible}
        .dashboard-egg-experience[data-phase="quote"] .dashboard-egg-stage{opacity:0;transform:translate3d(0,-4px,0) scale3d(0.96,0.96,1);filter:blur(4px)}
        .dashboard-egg-quote{position:absolute;inset:clamp(16px,2vw,22px);z-index:2;display:grid;grid-template-columns:minmax(0,1fr);gap:7px;min-height:86px;align-content:center;margin:0;color:var(--color-on-surface);opacity:0;pointer-events:none;transform:scale3d(0.92,0.92,1);transition:opacity 420ms ease,transform 520ms cubic-bezier(0.16,1,0.3,1)}
        .dashboard-egg-experience.is-complete .dashboard-egg-quote{position:relative;inset:auto;transform:scale3d(0.98,0.98,1)}
        .dashboard-egg-quote.is-visible{opacity:1;pointer-events:auto;transform:scale3d(1,1,1)}
        .dashboard-egg-experience.is-complete .dashboard-egg-quote.is-visible{transform:scale3d(1,1,1)}
        .dashboard-editing-layout .dashboard-egg-quote{padding-inline:38px}
        .dashboard-egg-quote p{max-width:920px;margin:0 auto;color:var(--color-on-surface);font-size:clamp(16px,2vw,22px);font-weight:760;line-height:1.25;letter-spacing:0}
        .dashboard-egg-quote cite{color:var(--color-on-surface-variant);font-size:13px;font-style:normal;font-weight:720;text-align:center}
        .dashboard-egg-quote-remove{position:absolute;right:clamp(10px,1.4vw,16px);top:clamp(10px,1.4vw,16px);z-index:4;display:none;width:30px;height:30px;align-items:center;justify-content:center;border:1px solid color-mix(in srgb,var(--color-outline-variant) 84%,transparent);border-radius:999px;background:color-mix(in srgb,var(--color-on-surface-variant) 34%,var(--color-surface-container-high));color:var(--color-error);box-shadow:0 10px 26px rgba(0,0,0,0.24);cursor:pointer}
        .dashboard-editing-layout .dashboard-egg-quote-remove{display:inline-flex}
        .dashboard-egg-quote-remove:hover,.dashboard-egg-quote-remove:focus-visible{background:color-mix(in srgb,var(--color-on-surface-variant) 42%,var(--color-surface-container-high));outline:none}
        .dashboard-egg-quote-remove .material-symbols-outlined{font-size:18px}
        @keyframes dashboardEggDrop{from{transform:translate3d(0,18px,0) scale3d(0,0,1)}
        to{transform:translate3d(0,0,0) scale3d(1,1,1)}
        }
        @keyframes dashboardEggWobble{from{transform:translate3d(0,0,0) rotate3d(0,0,1,calc(var(--dashboard-egg-wobble) * -1)) scale3d(1,1,1)}
        to{transform:translate3d(0,0,0) rotate3d(0,0,1,var(--dashboard-egg-wobble)) scale3d(1,1,1)}
        }
        @keyframes dashboardEggCorePulse{0%,100%{transform:translate3d(-50%,-50%,0) scale3d(0.82,0.82,1);opacity:0.76}
        50%{transform:translate3d(-50%,-50%,0) scale3d(1.08,1.08,1);opacity:1}
        }
        @media (prefers-reduced-motion:reduce){.dashboard-egg-experience,.dashboard-egg-drop,.dashboard-egg-figure,.dashboard-egg-shell,.dashboard-egg-half,.dashboard-egg-aura,.dashboard-egg-core,.dashboard-egg-quote{animation:none !important;transition:none !important}
        }
        @media (max-width:640px){.dashboard-egg-experience{min-height:136px}
        .dashboard-egg-experience.is-complete{min-height:100px}
        .dashboard-egg-quote{min-height:100px}
        .dashboard-editing-layout .dashboard-egg-quote{padding-inline:34px}
        }
    `;

    function injectStyles() {
        if (document.getElementById(STYLE_ID)) return;
        const style = document.createElement("style");
        style.id = STYLE_ID;
        style.textContent = css;
        document.head.appendChild(style);
    }

    function buildExperience(slot) {
        slot.innerHTML = `
            <div class="dashboard-egg-experience" data-phase="drop" role="status" aria-label="Preparing daily motivation">
                <div class="dashboard-egg-stage" aria-hidden="true">
                    <div class="dashboard-egg-aura"></div>
                    <div class="dashboard-egg-core"></div>
                    <div class="dashboard-egg-drop">
                        <div class="dashboard-egg-figure">
                            <div class="dashboard-egg-shell"></div>
                            <div class="dashboard-egg-particles"></div>
                        </div>
                    </div>
                    <span class="dashboard-egg-loading-text">Opening today's quote...</span>
                </div>
                <figure class="dashboard-egg-quote">
                    <button class="dashboard-egg-quote-remove" type="button" aria-label="Hide daily quote">
                        <span class="material-symbols-outlined" aria-hidden="true">close</span>
                    </button>
                    <p></p>
                    <cite></cite>
                </figure>
            </div>
        `;
        return slot.querySelector(".dashboard-egg-experience");
    }

    function addEggSplitGeometry(root) {
        const figure = root.querySelector(".dashboard-egg-figure");
        if (!figure || figure.querySelector(".dashboard-egg-half")) return;
        const particles = figure.querySelector(".dashboard-egg-particles");
        const fragment = document.createDocumentFragment();
        const left = document.createElement("div");
        left.className = "dashboard-egg-half dashboard-egg-half-left";
        const right = document.createElement("div");
        right.className = "dashboard-egg-half dashboard-egg-half-right";
        fragment.append(left, right);
        figure.insertBefore(fragment, particles);
    }

    function ensureQuoteShell(slot) {
        let root = slot.querySelector(".dashboard-egg-experience");
        if (root) return root;
        slot.innerHTML = '<div class="dashboard-egg-experience is-complete" data-phase="quote"></div>';
        return slot.querySelector(".dashboard-egg-experience");
    }

    function bindQuoteRemove(slot, banner) {
        const button = banner.querySelector(".dashboard-egg-quote-remove");
        if (!button || button.dataset.bound === "true") return;
        button.dataset.bound = "true";
        button.addEventListener("click", (event) => {
            event.preventDefault();
            event.stopPropagation();
            hideQuote(slot);
            window.APStudyUndo?.stage?.({
                message: "Daily quote hidden.",
                restore: () => showQuote(slot),
            });
        });
    }

    function ensureQuoteBanner(slot) {
        const root = ensureQuoteShell(slot);
        let banner = root.querySelector(".dashboard-egg-quote");
        if (banner) {
            bindQuoteRemove(slot, banner);
            return banner;
        }
        banner = document.createElement("figure");
        banner.className = "dashboard-egg-quote";
        banner.innerHTML = `
            <button class="dashboard-egg-quote-remove" type="button" aria-label="Hide daily quote">
                <span class="material-symbols-outlined" aria-hidden="true">close</span>
            </button>
            <p></p>
            <cite></cite>
        `;
        root.appendChild(banner);
        bindQuoteRemove(slot, banner);
        return banner;
    }

    function renderQuote(slot, quote) {
        const root = ensureQuoteShell(slot);
        const banner = ensureQuoteBanner(slot);
        const text = banner.querySelector("p");
        const author = banner.querySelector("cite");
        if (text) text.textContent = quote.text;
        if (author) author.textContent = quote.author ? `- ${quote.author}` : "";
        root.dataset.phase = "quote";
        requestAnimationFrame(() => {
            root.classList.add("is-complete");
            banner.classList.add("is-visible");
        });
        return banner;
    }

    function hideQuote(slot, persist = true) {
        if (persist) setVisible(false);
        slot.innerHTML = "";
    }

    function showQuote(slot, persist = true) {
        if (persist) setVisible(true);
        slot.hidden = false;
        slot.innerHTML = "";
        slot.dataset.eggInitialized = "";
        init({ skipAnimation: true, forceVisible: true });
    }

    function runParticles(root) {
        const field = root.querySelector(".dashboard-egg-particles");
        if (!field) return () => {};

        const particles = Array.from({ length: PARTICLE_COUNT }, (_, index) => {
            const element = document.createElement("span");
            element.className = "dashboard-egg-particle";
            const side = index % 2 === 0 ? -1 : 1;
            const speed = PARTICLE_BURST_SPEED * (0.72 + Math.random() * 0.72);
            const particle = {
                element,
                x: side * (6 + Math.random() * 12),
                y: -8 + Math.random() * 16,
                vx: side * speed,
                vy: -0.22 - Math.random() * 0.17,
                rotation: Math.random() * 80 - 40,
                vr: side * (0.34 + Math.random() * 0.42),
                scale: 0.72 + Math.random() * 0.72,
                opacity: 1,
            };
            field.appendChild(element);
            return particle;
        });

        let frameId = 0;
        let startedAt = 0;
        let last = 0;

        function tick(now) {
            if (!startedAt) {
                startedAt = now;
                last = now;
            }
            const elapsed = now - startedAt;
            const dt = Math.min(34, now - last);
            last = now;

            particles.forEach((particle) => {
                particle.vy += PARTICLE_GRAVITY * dt;
                particle.vx *= PARTICLE_DRAG;
                particle.vy *= PARTICLE_DRAG;
                particle.x += particle.vx * dt;
                particle.y += particle.vy * dt;
                particle.rotation += particle.vr * dt;
                particle.opacity = Math.max(0, 1 - elapsed / PARTICLE_TTL_MS);
                particle.element.style.opacity = String(particle.opacity);
                particle.element.style.transform = `translate3d(${particle.x}px, ${particle.y}px, 0) rotate3d(0, 0, 1, ${particle.rotation}deg) scale3d(${particle.scale}, ${particle.scale}, 1)`;
            });

            if (elapsed < PARTICLE_TTL_MS) {
                frameId = requestAnimationFrame(tick);
            } else {
                particles.forEach((particle) => particle.element.remove());
            }
        }

        frameId = requestAnimationFrame(tick);
        return () => {
            cancelAnimationFrame(frameId);
            particles.forEach((particle) => particle.element.remove());
        };
    }

    function init(options = {}) {
        const slot = document.getElementById(SLOT_ID);
        if (!slot || slot.dataset.eggInitialized === "true") return;
        if (!options.forceVisible && !isVisible()) {
            slot.innerHTML = "";
            window.APStudyDashboardDailyQuote = api;
            return;
        }
        slot.dataset.eggInitialized = "true";
        injectStyles();

        const dateKey = getDailyKey();
        const cachedQuote = readCache(dateKey);
        if (cachedQuote) {
            renderQuote(slot, cachedQuote);
            return;
        }

        if (options.skipAnimation) {
            const controller = new AbortController();
            const timeoutId = window.setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
            fetchQuote(controller.signal, { dateKey, phase: "skip_animation" }).then((nextQuote) => {
                window.clearTimeout(timeoutId);
                writeCache(dateKey, nextQuote);
                renderQuote(slot, nextQuote);
            });
            return;
        }

        const prefersReducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
        const controller = new AbortController();
        const timers = new Set();
        let clearParticles = () => {};
        let quote = null;
        let waitingForQuote = false;
        let revealed = false;

        const timeoutId = window.setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
        timers.add(timeoutId);

        const quotePromise = fetchQuote(controller.signal, { dateKey, phase: "animation" }).then((nextQuote) => {
            window.clearTimeout(timeoutId);
            timers.delete(timeoutId);
            quote = nextQuote;
            writeCache(dateKey, quote);
            if (waitingForQuote) reveal();
            return quote;
        });

        if (prefersReducedMotion) {
            quotePromise.then((nextQuote) => renderQuote(slot, nextQuote));
            return;
        }

        const root = buildExperience(slot);

        function setTimer(callback, delay) {
            const id = window.setTimeout(() => {
                timers.delete(id);
                callback();
            }, delay);
            timers.add(id);
            return id;
        }

        function reveal() {
            if (revealed || !quote) return;
            revealed = true;
            waitingForQuote = false;
            clearParticles();
            renderQuote(slot, quote);
            setTimer(() => {
                root.classList.add("is-complete");
                root.querySelector(".dashboard-egg-stage")?.remove();
            }, 540);
        }

        setTimer(() => {
            addEggSplitGeometry(root);
            root.dataset.phase = "fracture";
            clearParticles = runParticles(root);
        }, PHASE_FRACTURE_MS);

        setTimer(() => {
            root.dataset.phase = "split";
        }, PHASE_SPLIT_MS);

        setTimer(() => {
            if (quote) {
                reveal();
                return;
            }
            waitingForQuote = true;
            root.dataset.phase = "hold";
        }, PHASE_REVEAL_GATE_MS);

        window.addEventListener("pagehide", () => {
            timers.forEach((id) => window.clearTimeout(id));
            timers.clear();
            clearParticles();
            controller.abort();
        }, { once: true });
    }

    const api = {
        isHidden: () => !isVisible(),
        hide: () => {
            const slot = document.getElementById(SLOT_ID);
            if (slot) hideQuote(slot);
        },
        show: () => {
            const slot = document.getElementById(SLOT_ID);
            if (slot) showQuote(slot);
        },
        previewVisibility: (visible) => {
            const slot = document.getElementById(SLOT_ID);
            if (!slot) return;
            if (visible && !slot.innerHTML.trim()) showQuote(slot, false);
            slot.hidden = !visible;
        },
        commitVisibility: (visible) => {
            const slot = document.getElementById(SLOT_ID);
            setVisible(Boolean(visible));
            if (!slot) return;
            if (visible) showQuote(slot, false);
            else hideQuote(slot, false);
            slot.hidden = !visible;
        },
    };
    window.APStudyDashboardDailyQuote = api;

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init, { once: true });
    } else {
        init();
    }
})();
