// Form-flow page — three-step form. Each Next button posts the current step
// and advances to the next. The monitor dashboard renders each post as a row.

(function () {
  const steps = Array.from(document.querySelectorAll(".step"));
  const status = document.getElementById("status");

  function advance(currentIndex) {
    steps[currentIndex].classList.remove("step--active");
    steps[currentIndex].classList.add("step--done");
    const next = steps[currentIndex + 1];
    if (next) {
      next.classList.add("step--active");
      const firstInput = next.querySelector("input, textarea");
      if (firstInput) firstInput.focus();
    }
  }

  async function postStep(step, label, value) {
    const res = await fetch("/api/form-step", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ step, label, value }),
    });
    if (!res.ok) {
      status.textContent = `step ${step} failed: ${res.status}`;
      status.classList.remove("status--success");
      return false;
    }
    return true;
  }

  document.getElementById("next-1").addEventListener("click", async () => {
    const value = document.getElementById("name").value || "";
    if (!(await postStep(1, "name", value))) return;
    status.textContent = "step 1 sent";
    status.classList.add("status--success");
    advance(0);
  });

  document.getElementById("next-2").addEventListener("click", async () => {
    const value = document.getElementById("email").value || "";
    if (!(await postStep(2, "email", value))) return;
    status.textContent = "step 2 sent";
    status.classList.add("status--success");
    advance(1);
  });

  document.getElementById("submit").addEventListener("click", async () => {
    const value = document.getElementById("notes").value || "";
    if (!(await postStep(3, "notes", value))) return;
    status.textContent = "submitted";
    status.classList.add("status--success");
    steps[2].classList.remove("step--active");
    steps[2].classList.add("step--done");
  });
})();
