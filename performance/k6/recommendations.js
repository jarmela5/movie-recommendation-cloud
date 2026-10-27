import http from "k6/http";
import { check, sleep } from "k6";
const BASE_URL = __ENV.BASE_URL || "http://localhost:8080";
let sessionToken = null;
let initialized = false;

export const options = {
  vus: 5,
  duration: "60s",
  summaryTrendStats: ["avg", "min", "med", "max", "p(90)", "p(95)", "p(99)"],
};

export default function () {
  if (!initialized) {
    const id = `${__VU}-${Date.now()}`;

    const register = http.post(
      `${BASE_URL}/api/user/register`,
      JSON.stringify({
        username: `loadtest-${id}`,
        email: `loadtest-${id}@example.com`,
        password: "loadtest-password",
      }),
      {
        headers: { "Content-Type": "application/json" },
      },
    );

    check(register, {
      "registration succeeded": (r) => r.status === 201,
    });


    sessionToken = register.cookies.session[0].value;

    initialized = true;
  }

  const response = http.post(
    `${BASE_URL}/api/recommendations/generate`,
    JSON.stringify({ limit: 5 }),
    {
      headers: { "Content-Type": "application/json", Cookie: `session=${sessionToken}` },
    },
  );

  check(response, {
    "recommendations succeeded": (r) => r.status === 200,
  });

  sleep(1);
}
