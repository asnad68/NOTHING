import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPENAPI_PATH = ROOT / "api/openapi.json"


class OpenApiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.spec = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))

    def test_openapi_version_and_dialect(self) -> None:
        self.assertEqual(self.spec["openapi"], "3.1.2")
        self.assertEqual(
            self.spec["jsonSchemaDialect"],
            "https://json-schema.org/draft/2020-12/schema",
        )

    def test_required_paths_exist(self) -> None:
        paths = self.spec["paths"]
        expected = {
            "/v1/identity/{nothing_id}",
            "/v1/evidence/{evidence_id}",
            "/v1/verification-events/{event_id}",
            "/v1/procedures/{procedure_id}/{version}",
        }
        self.assertTrue(expected.issubset(paths))

    def resolve_ref(self, value: dict) -> dict:
        ref = value.get("$ref")
        if not ref:
            return value
        self.assertTrue(ref.startswith("#/components/"))
        _, _, section, name = ref.split("/", 3)
        return self.spec["components"][section][name]

    def test_path_parameters_are_declared(self) -> None:
        for path, item in self.spec["paths"].items():
            placeholders = re.findall(r"{([^}]+)}", path)
            for operation in item.values():
                if not isinstance(operation, dict):
                    continue
                parameter_names = {
                    self.resolve_ref(parameter)["name"]
                    for parameter in operation.get("parameters", [])
                }
                for placeholder in placeholders:
                    self.assertIn(
                        placeholder,
                        parameter_names,
                        f"{path} missing declaration for {placeholder}",
                    )

    def test_identity_operation_has_cache_and_error_contract(self) -> None:
        operation = self.spec["paths"]["/v1/identity/{nothing_id}"]["get"]
        self.assertIn("200", operation["responses"])
        self.assertIn("304", operation["responses"])
        self.assertIn("400", operation["responses"])
        self.assertIn("404", operation["responses"])
        self.assertIn("429", operation["responses"])
        self.assertIn("503", operation["responses"])
        self.assertIn("If-None-Match", {
            self.resolve_ref(parameter)["name"] for parameter in operation["parameters"]
        })
        self.assertIn("application/problem+json",
                      operation["responses"]["404"]["content"])

    def test_all_internal_refs_resolve_to_components(self) -> None:
        components = self.spec["components"]

        def check(value):
            if isinstance(value, dict):
                if "$ref" in value:
                    ref = value["$ref"]
                    self.assertTrue(
                        ref.startswith("#/components/"),
                        f"unexpected non-component ref: {ref}",
                    )
                    target = ref.split("/")[2:]
                    current = components
                    for part in target:
                        self.assertIn(part, current)
                        current = current[part]
                for child in value.values():
                    check(child)
            elif isinstance(value, list):
                for child in value:
                    check(child)

        check(self.spec["paths"])

    def test_demo_response_matches_basic_contract(self) -> None:
        response = json.loads(
            (ROOT / "api/examples/get-identity-200.json").read_text(
                encoding="utf-8"
            )
        )
        data = response["data"]
        self.assertRegex(data["id"], r"^NTH-[0-9]{6}$")
        self.assertTrue(data["claims"])
        self.assertRegex(data["claims"][0]["id"], r"^CLM-[0-9]{6}$")
        self.assertEqual(
            data["claims"][0]["current_verification"]["procedure"]["version"],
            "0.1",
        )



    def test_authenticated_ingestion_contract(self) -> None:
        operation = self.spec["paths"]["/v1/ingestion/bundles"]["post"]
        self.assertEqual(operation["security"], [{"BearerAuth": []}])
        parameter_names = {
            self.resolve_ref(parameter)["name"]
            for parameter in operation["parameters"]
        }
        self.assertIn("Idempotency-Key", parameter_names)
        for status in ("200", "400", "401", "403", "409", "413", "415", "422", "429", "503"):
            self.assertIn(status, operation["responses"])
        self.assertIn(
            "application/problem+json",
            operation["responses"]["401"]["content"],
        )
        self.assertIn(
            "BearerAuth",
            self.spec["components"]["securitySchemes"],
        )

if __name__ == "__main__":
    unittest.main()
