"""Federation role selection regression tests."""
import pytest

from app.services.server_federation import NodeRole, role_for_federation_node


@pytest.mark.parametrize(
    ("node_id", "expected"),
    [("hetzner", NodeRole.HUB),
     ("backup", NodeRole.NODE),
     ("zombie-pc", NodeRole.NODE),
     ("custom-node", NodeRole.NODE)],
)
def test_federation_runtime_role_mapping(node_id, expected):
    assert role_for_federation_node(node_id) == expected
