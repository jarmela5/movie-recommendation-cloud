"""Offline configuration checks. Run from any directory with Python + PyYAML."""
from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]
DOCS = [doc for path in (ROOT / "k8s").glob("*.yaml")
        for doc in yaml.safe_load_all(path.read_text()) if doc]
DEPLOYMENTS = {d["metadata"]["name"]: d for d in DOCS if d["kind"] == "Deployment"}
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]


def pod(name):
    return DEPLOYMENTS[name]["spec"]["template"]["spec"]


def container(name):
    return pod(name)["containers"][0]


def env(name):
    return {e["name"]: e for e in container(name).get("env", [])}


class KubernetesIntegration(unittest.TestCase):
    def test_inference_matches_tested_compose_model(self):
        self.assertEqual(container("inference")["image"], COMPOSE["inference"]["image"])
        self.assertEqual(container("inference")["args"], COMPOSE["inference"]["command"])
        for key in ("INFERENCE_SERVICE_URL", "INFERENCE_MODEL", "INFERENCE_TIMEOUT_SECONDS",
                    "USER_SERVICE_URL", "CATALOG_SERVICE_URL", "RECOMMENDATIONS_DB_PATH"):
            self.assertEqual(env("recommendations")[key]["value"], COMPOSE["recommendations"]["environment"][key])

    def test_internal_token_is_shared_and_not_literal(self):
        for name in ("user", "recommendations", "agent"):
            self.assertNotIn("value", env(name)["INTERNAL_API_TOKEN"])
            self.assertEqual(env(name)["INTERNAL_API_TOKEN"]["valueFrom"]["secretKeyRef"],
                             {"name": "internal-api", "key": "token"})

    def test_sqlite_and_model_volumes_have_single_owner_during_updates(self):
        claims = {d["metadata"]["name"] for d in DOCS if d["kind"] == "PersistentVolumeClaim"}
        for name, claim, mount in (("user", "user-data", "/app/data"),
                                   ("catalog", "catalog-data", "/app/data"),
                                   ("recommendations", "recommendations-data", "/app/data"),
                                   ("inference", "model-cache", "/models")):
            self.assertEqual(DEPLOYMENTS[name]["spec"]["replicas"], 1)
            self.assertEqual(DEPLOYMENTS[name]["spec"]["strategy"]["type"], "Recreate")
            self.assertIn(claim, claims)
            volume = next(v for v in pod(name)["volumes"] if v.get("persistentVolumeClaim", {}).get("claimName") == claim)
            self.assertIn({"name": volume["name"], "mountPath": mount}, container(name)["volumeMounts"])

    def test_agent_is_only_a_client_and_reuses_recommendations_image(self):
        self.assertEqual(container("agent")["image"], container("recommendations")["image"])
        self.assertEqual(container("agent")["command"], COMPOSE["agent"]["command"])
        self.assertFalse(container("agent").get("ports"))
        self.assertFalse(pod("agent").get("volumes"))
        self.assertGreaterEqual(pod("agent")["terminationGracePeriodSeconds"], 120)
        self.assertEqual(DEPLOYMENTS["agent"]["spec"]["replicas"], 1)
        self.assertEqual(DEPLOYMENTS["agent"]["spec"]["strategy"]["type"], "Recreate")

    def test_services_resolve_existing_pods_and_keep_ai_internal(self):
        for service in (d for d in DOCS if d["kind"] == "Service"):
            selector = service["spec"]["selector"]
            matches = [d for d in DEPLOYMENTS.values() if
                       selector.items() <= d["spec"]["template"]["metadata"]["labels"].items()]
            self.assertTrue(matches, service["metadata"]["name"])
            if service["metadata"]["name"] in ("inference", "recommendations", "user", "catalog"):
                self.assertEqual(service["spec"].get("type", "ClusterIP"), "ClusterIP")

    def test_nginx_timeout_and_slow_model_startup(self):
        config = next(d["data"]["default.conf"] for d in DOCS
                      if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "frontend-nginx-conf")
        self.assertIn("proxy_read_timeout 210s;", config)
        self.assertNotIn("location /internal", config)
        startup = container("inference")["startupProbe"]
        self.assertGreaterEqual(startup["periodSeconds"] * startup["failureThreshold"], 900)
        self.assertEqual(startup["httpGet"], {"path": "/health", "port": 8080})


if __name__ == "__main__":
    unittest.main(verbosity=2)
