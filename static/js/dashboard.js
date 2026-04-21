document.addEventListener("DOMContentLoaded", () => {
    const heading = document.querySelector(
        "section h1.text-3xl.font-headline"
    );
    if (heading) {
        heading.textContent = new Date().toLocaleString("en-US", {
            month: "long",
            year: "numeric",
        });
    }
});
