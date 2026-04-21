document.querySelector("global.thenav").innerHTML = `
<header class="bg-[#1c2026]/60 backdrop-blur-md fixed top-0 w-full z-50 flex justify-between items-center h-16 px-8 shadow-2xl shadow-black/40 border-b border-[#181c22]">
    <div class="flex items-center gap-4">
        <span class="text-xl font-semibold tracking-tighter text-[#c0c7d4]">Canvas.APStudy.org</span>
    </div>
    <div class="flex items-center gap-4">
        <button class="text-[#c0c7d4]/60 hover:text-[#a2c9ff] transition-all duration-150 ease-in-out active:scale-95 p-2 rounded-full">
            <span class="material-symbols-outlined" data-icon="sync">sync</span>
        </button>
        <button class="text-[#a2c9ff] hover:bg-[#262a31] transition-all duration-150 ease-in-out active:scale-95 p-2 rounded-full">
            <span class="material-symbols-outlined" data-icon="settings">settings</span>
        </button>
        <button class="text-[#c0c7d4]/60 hover:text-[#a2c9ff] transition-all duration-150 ease-in-out active:scale-95 p-2 rounded-full">
            <span class="material-symbols-outlined" data-icon="logout">logout</span>
        </button>
        <div class="w-8 h-8 rounded-full bg-surface-container-high overflow-hidden ml-2 border border-outline-variant/30">
            <img alt="Profile" class="w-full h-full object-cover" src="https://lh3.googleusercontent.com/aida-public/AB6AXuBIfYSsGVwQhSKDisBejBzu2WjfUSy7ZvB6EuSniyEVL0AFAL-zPWMSUf7nY7dcreb3wFIGRN0FldnYUKwUD8biMdNGR7mQgOBdpWxWYeAOZ3T6RxewSCPkDsTNfT9wHiMcWitHbciCn4Rdm0e4jxbaEEd1UxWduW8n_MF_2DUm_MfIUs2TnGVWHOV7I9vPjdY_PQYLR9EDW4JqkFUaA3SeQRORrzX7nb7lO2JSgUCGjY36VsPPGZjED0Zc56B7JbjQhDVVIa6TIdY"/>
        </div>
    </div>
</header>
`
document.querySelector("global.thefooter").innerHTML=`
<footer class="bg-[#10141a] w-full py-12 border-t border-[#181c22]">
    <div class="flex flex-col md:flex-row justify-between items-center px-12 max-w-7xl mx-auto">
        <span class="font-['Inter'] text-[11px] uppercase tracking-[0.05em] font-normal text-[#c0c7d4]/40">© 2024 Emory.APStudy.org. Built for Emory University by an Emory Student.</span>
        <div class="flex gap-6 mt-4 md:mt-0">
            <a class="font-['Inter'] text-[11px] uppercase tracking-[0.05em] font-normal text-[#c0c7d4]/40 hover:text-[#a2c9ff] transition-colors" href="#">Support</a>
            <a class="font-['Inter'] text-[11px] uppercase tracking-[0.05em] font-normal text-[#c0c7d4]/40 hover:text-[#a2c9ff] transition-colors" href="#">Archive</a>
            <a class="font-['Inter'] text-[11px] uppercase tracking-[0.05em] font-normal text-[#c0c7d4]/40 hover:text-[#a2c9ff] transition-colors" href="#">Privacy</a>
        </div>
    </div>
</footer>
`