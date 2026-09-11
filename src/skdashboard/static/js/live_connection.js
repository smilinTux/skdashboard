const LABELS = {
  connected: "connected",
  retrying: "retrying",
  polling: "polling fallback",
  unauthorized: "sign in required",
  offline: "offline",
};

export function createLiveConnection({ dot, text, refresh, signIn, EventSourceClass = EventSource, retryLimit = 3 }) {
  let stream;
  let failures = 0;
  let pollSucceeded = false;

  const setState = (state) => {
    dot.classList.toggle("on", state === "connected");
    dot.dataset.state = state;
    text.textContent = LABELS[state];
  };

  const start = () => {
    setState("retrying");
    stream = new EventSourceClass("/api/v1/events");
    stream.addEventListener("open", () => {
      failures = 0;
      setState("connected");
    });
    stream.addEventListener("board_changed", refresh);
    stream.addEventListener("card_changed", refresh);
    stream.addEventListener("error", () => {
      failures += 1;
      if (failures >= retryLimit) stream.close();
      setState(pollSucceeded ? "polling" : navigator.onLine === false ? "offline" : "retrying");
      refresh();
    });
  };

  return {
    start,
    pollSucceeded() {
      pollSucceeded = true;
      if (!stream || stream.readyState !== EventSourceClass.OPEN) setState("polling");
    },
    pollFailed(error) {
      pollSucceeded = false;
      if (error && error.status === 401) {
        if (stream) stream.close();
        setState("unauthorized");
        void signIn();
      } else {
        setState(navigator.onLine === false || !error || !error.status ? "offline" : "retrying");
      }
    },
  };
}
