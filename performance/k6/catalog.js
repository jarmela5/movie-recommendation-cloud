import http from "k6/http";
import { check, sleep } from "k6";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8080";

export const options = {
  vus: 5,
  duration: "60s",
  summaryTrendStats: ["avg", "min", "med", "max", "p(90)", "p(95)", "p(99)"],
};

export default function () {
  const response = http.get(`${BASE_URL}/api/movies`);

  check(response, {
    "status is 200": (r) => r.status === 200,
  });

  sleep(1);
}
