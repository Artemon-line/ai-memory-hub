document.querySelectorAll("button[data-copy]").forEach((button) => {
  const defaultText = button.textContent.trim();
  button.addEventListener("click", () => {
    const target = document.getElementById(button.dataset.copy);
    if (!target) return;

    navigator.clipboard.writeText(target.value).then(() => {
      button.textContent = "Copied";
      button.classList.add("is-copied");
      window.setTimeout(() => {
        button.textContent = defaultText;
        button.classList.remove("is-copied");
      }, 1400);
    });
  });
});
