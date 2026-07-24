document.querySelectorAll("button[data-copy]").forEach((button) => {
  button.addEventListener("click", () => {
    const target = document.getElementById(button.dataset.copy);
    if (target) navigator.clipboard.writeText(target.value);
  });
});
