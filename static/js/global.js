function renderGlobalChrome() {
    const nav = document.querySelector("global.thenav");
    if (nav) {
        nav.style.position = "relative";
        nav.style.zIndex = "9999";

        const profileImage =
            nav.dataset.profilePicture ||
            document.body?.dataset?.profilePicture ||
            "https://lh3.googleusercontent.com/aida-public/AB6AXuBIfYSsGVwQhSKDisBejBzu2WjfUSy7ZvB6EuSniyEVL0AFAL-zPWMSUf7nY7dcreb3wFIGRN0FldnYUKwUD8biMdNGR7mQgOBdpWxWYeAOZ3T6RxewSCPkDsTNfT9wHiMcWitHbciCn4Rdm0e4jxbaEEd1UxWduW8n_MF_2DUm_MfIUs2TnGVWHOV7I9vPjdY_PQYLR9EDW4JqkFUaA3SeQRORrzX7nb7lO2JSgUCGjY36VsPPGZjED0Zc56B7JbjQhDVVIa6TIdY";
        let syncButton = null;
        let syncIcon = null;
        let syncLocked = false;
        let syncSuccess = false;

        function setSyncIcon(name) {
            if (!syncIcon) {
                return;
            }
            syncIcon.textContent = name;
            syncIcon.dataset.icon = name;
        }

        function setSyncButtonLocked(locked) {
            if (!syncButton) {
                return;
            }
            syncButton.disabled = locked;
            syncButton.setAttribute("aria-disabled", String(locked));
            syncButton.classList.toggle("cursor-not-allowed", locked);
            syncButton.classList.toggle("opacity-50", locked);
        }

        function showSyncSuccess() {
            syncLocked = false;
            syncSuccess = true;
            setSyncButtonLocked(false);
            setSyncIcon("check");
        }

        function showSyncFailure() {
            syncLocked = false;
            syncSuccess = false;
            setSyncButtonLocked(false);
            setSyncIcon("sync");
        }

        async function runSyncRefresh() {
            if (syncLocked) {
                return;
            }

            syncLocked = true;
            syncSuccess = false;
            setSyncButtonLocked(true);
            setSyncIcon("sync");

            window.setTimeout(async () => {
                try {
                    const response = await fetch("/api/calendar/refresh", {
                        method: "POST",
                        credentials: "same-origin",
                    });
                    if (!response.ok) {
                        throw new Error("Refresh failed");
                    }
                    showSyncSuccess();
                } catch (error) {
                    console.error(error);
                    showSyncFailure();
                }
            }, 10000);
        }

        nav.innerHTML = `
<header class="bg-surface-container/80 backdrop-blur-md top-0 w-full flex justify-between items-center h-16 px-8 shadow-2xl shadow-black/20 border-b border-outline-variant/30" style="position: relative; z-index: 9999;">
    <div class="flex items-center gap-4">
        <a href="/dashboard" class="text-xl font-semibold tracking-tighter text-on-surface-variant hover:text-primary transition-colors no-underline">Emory.APStudy.org</a>
    </div>
    <div class="flex items-center gap-3">
        <button id="courses-open-btn" type="button" class="text-sm font-medium px-3 py-1.5 rounded-full border border-outline-variant/40 bg-surface-container-high text-on-surface-variant hover:text-on-surface hover:border-primary/40 hover:bg-surface-container transition-colors" title="Search courses">
            Courses
        </button>
        <button aria-disabled="true" type="button" class="text-on-surface-variant/60 p-2 rounded-full cursor-default" title="Reload">
            <span class="material-symbols-outlined" data-icon="sync">sync</span>
        </button>
    <div class="relative" style="z-index: 10000;">
        <button id="profile-menu-trigger" aria-expanded="false" aria-haspopup="true" class="w-9 h-9 rounded-full bg-surface-container-high overflow-hidden border border-outline-variant/30 hover:border-primary transition-colors">
            <img alt="Profile" class="w-full h-full object-cover" src="${profileImage}"/>
        </button>
        <div id="profile-menu" class="hidden absolute right-0 mt-2 w-44 bg-surface border border-outline-variant/30 rounded-xl shadow-2xl shadow-black/20 overflow-hidden" style="z-index: 10001;">
            <button id="profile-settings" type="button" class="w-full text-left flex items-center gap-2 px-4 py-3 text-sm text-on-surface-variant hover:bg-surface-container hover:text-primary transition-colors">
                <span class="material-symbols-outlined text-[18px]">settings</span>
                Settings
            </button>
            <button id="profile-logout" type="button" class="w-full text-left flex items-center gap-2 px-4 py-3 text-sm text-on-surface-variant hover:bg-surface-container hover:text-primary transition-colors border-t border-outline-variant/30">
                <span class="material-symbols-outlined text-[18px]">logout</span>
                Logout
            </button>
        </div>
    </div>
    </div>
</header>
`;
    syncButton = nav.querySelector('button[title="Reload"]');
    syncIcon = syncButton?.querySelector(".material-symbols-outlined");
        const menuTrigger = nav.querySelector("#profile-menu-trigger");
        const menu = nav.querySelector("#profile-menu");
        const settingsButton = nav.querySelector("#profile-settings");
        const logoutButton = nav.querySelector("#profile-logout");
        if (menuTrigger && menu) {
            menuTrigger.addEventListener("click", (event) => {
                event.stopPropagation();
                const isOpen = !menu.classList.contains("hidden");
                menu.classList.toggle("hidden", isOpen);
                menuTrigger.setAttribute("aria-expanded", String(!isOpen));
            });
            document.addEventListener("click", (event) => {
                if (!menu.contains(event.target) && !menuTrigger.contains(event.target)) {
                    menu.classList.add("hidden");
                    menuTrigger.setAttribute("aria-expanded", "false");
                }
            });
            if (settingsButton) {
                settingsButton.addEventListener("click", () => {
                    window.location.assign(`${window.location.origin}/settings`);
                });
            }
            if (logoutButton) {
                logoutButton.addEventListener("click", () => {
                    window.location.assign(`${window.location.origin}/logout`);
                });
            }
        }
        if (syncButton && syncIcon) {
            syncButton.addEventListener("click", () => {
                runSyncRefresh();
            });

            syncButton.addEventListener("mouseenter", () => {
                if (syncSuccess) {
                    setSyncIcon("check");
                }
            });

            syncButton.addEventListener("mouseleave", () => {
                if (syncSuccess) {
                    syncSuccess = false;
                    setSyncIcon("sync");
                }
            });
        }
    }
    const footer = document.querySelector("global.thefooter");
    if (footer) {
        footer.innerHTML = `
<footer class="bg-surface w-full py-12 border-t border-outline-variant/30">
    <div class="flex flex-col md:flex-row justify-between items-center px-12 max-w-7xl mx-auto">
        <span class="font-['Inter'] text-[11px] uppercase tracking-[0.05em] font-normal text-on-surface-variant/50">© 2026 Emory.APStudy.org. Built for Emory University by an Emory Student.</span>
        <div class="flex gap-6 mt-4 md:mt-0">
            <a class="font-['Inter'] text-[11px] uppercase tracking-[0.05em] font-normal text-on-surface-variant/50 hover:text-primary transition-colors" href="mailto:derekchenusa@gmail.com">Support</a>
            <a class="font-['Inter'] text-[11px] uppercase tracking-[0.05em] font-normal text-on-surface-variant/50 hover:text-primary transition-colors" href="#">Archive</a>
            <a class="font-['Inter'] text-[11px] uppercase tracking-[0.05em] font-normal text-on-surface-variant/50 hover:text-primary transition-colors" href="#">Privacy</a>
        </div>
    </div>
</footer>
`;
    }
}
if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", renderGlobalChrome);
} else {
    renderGlobalChrome();
}