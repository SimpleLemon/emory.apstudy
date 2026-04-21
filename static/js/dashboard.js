document.addEventListener("DOMContentLoaded", () => {
    const heading = document.getElementById("dashboard-current-month");
    if (heading) {
        heading.textContent = new Date().toLocaleString("en-US", {
            month: "long",
            year: "numeric",
        });
    }
});
