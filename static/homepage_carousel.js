(() => {
  function initializeCarousel(carousel) {
    if (carousel.dataset.carouselInitialized === "true") return;

    const track = carousel.querySelector("[data-carousel-track]");
    const slides = Array.from(carousel.querySelectorAll("[data-carousel-slide]"));
    if (!track || slides.length < 2) return;

    carousel.dataset.carouselInitialized = "true";
    carousel.classList.add("homepage-carousel");
    track.classList.add("homepage-carousel-track");
    carousel.setAttribute("role", "region");
    carousel.setAttribute("aria-roledescription", "carousel");
    if (!carousel.hasAttribute("aria-label")) {
      carousel.setAttribute("aria-label", "Content carousel");
    }
    track.setAttribute("role", "group");
    track.setAttribute("aria-label", "Carousel slides");
    track.setAttribute("aria-live", "off");
    track.tabIndex = 0;

    slides.forEach((slide, index) => {
      slide.classList.add("homepage-carousel-slide");
      slide.setAttribute("role", "group");
      slide.setAttribute("aria-roledescription", "slide");
      if (!slide.hasAttribute("aria-label")) {
        slide.setAttribute("aria-label", `Slide ${index + 1} of ${slides.length}`);
      }
    });

    const controls = document.createElement("div");
    controls.className = "homepage-carousel-controls";
    controls.setAttribute("role", "group");
    controls.setAttribute("aria-label", "Carousel slide indicators");

    const indicators = document.createElement("div");
    indicators.className = "homepage-carousel-indicators";
    indicators.setAttribute("role", "group");
    indicators.setAttribute("aria-label", "Choose slide");

    const indicatorButtons = slides.map((slide, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "homepage-carousel-indicator";
      button.setAttribute("aria-label", `Go to slide ${index + 1}`);
      button.addEventListener("click", () => {
        scrollToSlide(index);
        scheduleRotation();
      });
      indicators.append(button);
      return button;
    });

    controls.append(indicators);
    carousel.append(controls);

    function currentSlideIndex() {
      const trackCenter = track.getBoundingClientRect().left + track.clientWidth / 2;
      return slides.reduce((closestIndex, slide, index) => {
        const slideRect = slide.getBoundingClientRect();
        const slideCenter = slideRect.left + slideRect.width / 2;
        const closestRect = slides[closestIndex].getBoundingClientRect();
        const closestCenter = closestRect.left + closestRect.width / 2;
        return Math.abs(slideCenter - trackCenter) < Math.abs(closestCenter - trackCenter)
          ? index
          : closestIndex;
      }, 0);
    }

    function updateIndicators() {
      const activeIndex = currentSlideIndex();
      indicatorButtons.forEach((button, index) => {
        button.setAttribute("aria-current", String(index === activeIndex));
      });
    }

    function scrollToSlide(index) {
      const targetIndex = (index + slides.length) % slides.length;
      const trackRect = track.getBoundingClientRect();
      const slideRect = slides[targetIndex].getBoundingClientRect();
      track.scrollTo({
        left: track.scrollLeft + slideRect.left - trackRect.left,
        behavior: "instant",
      });
      updateIndicators();
    }

    carousel.addEventListener("keydown", (event) => {
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        scrollToSlide(currentSlideIndex() - 1);
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        scrollToSlide(currentSlideIndex() + 1);
      }
    });

    let updateScheduled = false;
    track.addEventListener(
      "scroll",
      () => {
        if (updateScheduled) return;
        updateScheduled = true;
        requestAnimationFrame(() => {
          updateScheduled = false;
          updateIndicators();
        });
      },
      { passive: true },
    );

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let rotationTimer;
    let pointerInside = false;
    let focusInside = false;

    function pauseRotation() {
      window.clearTimeout(rotationTimer);
      rotationTimer = undefined;
    }

    function scheduleRotation() {
      pauseRotation();
      if (reduceMotion || document.hidden || pointerInside || focusInside) return;
      rotationTimer = window.setTimeout(() => {
        scrollToSlide(currentSlideIndex() + 1);
        scheduleRotation();
      }, 5000);
    }

    carousel.addEventListener("pointerenter", () => {
      pointerInside = true;
      pauseRotation();
    });
    carousel.addEventListener("pointerleave", () => {
      pointerInside = false;
      scheduleRotation();
    });
    carousel.addEventListener("focusin", () => {
      focusInside = true;
      pauseRotation();
    });
    carousel.addEventListener("focusout", (event) => {
      if (!carousel.contains(event.relatedTarget)) {
        focusInside = false;
        scheduleRotation();
      }
    });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) {
        pauseRotation();
      } else {
        scheduleRotation();
      }
    });

    updateIndicators();
    scheduleRotation();
  }

  function initializeCarousels() {
    document.querySelectorAll("[data-homepage-carousel]").forEach(initializeCarousel);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initializeCarousels, { once: true });
  } else {
    initializeCarousels();
  }
})();
