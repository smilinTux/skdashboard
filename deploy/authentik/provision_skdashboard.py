"""Create or update SKDashboard's Authentik application, idempotently.

Runs inside `ak shell`. Prepend a CFG dict, so the client secret never reaches a
command line or a process environment:

    (echo "CFG = $(cat cfg.json)"; cat provision_skdashboard.py) | \
        docker exec -i <sksso-server> ak shell

CFG keys: slug, name, launch_url, redirect_uri, client_secret, admin_groups,
user_groups; optional category. The client id is always "skdashboard".
Prints status lines only, never the secret. Other applications are not touched.
"""

from authentik.core.models import Application, Group
from authentik.crypto.models import CertificateKeyPair
from authentik.flows.models import Flow
from authentik.policies.models import PolicyBinding
from authentik.providers.oauth2.models import (
    ClientTypes,
    OAuth2Provider,
    RedirectURI,
    RedirectURIMatchingMode,
    ScopeMapping,
    SubModes,
)

MANAGED_SCOPES = ["openid", "email", "profile", "offline_access"]

provider, created = OAuth2Provider.objects.update_or_create(
    name=CFG["slug"],  # noqa: F821
    defaults={
        "client_id": "skdashboard",
        "client_type": ClientTypes.CONFIDENTIAL,
        "client_secret": CFG["client_secret"],  # noqa: F821
        "authorization_flow": Flow.objects.get(
            slug="default-provider-authorization-implicit-consent"
        ),
        "invalidation_flow": Flow.objects.get(slug="default-provider-invalidation-flow"),
        "signing_key": CertificateKeyPair.objects.get(name="authentik Self-signed Certificate"),
        "sub_mode": SubModes.USER_USERNAME,
        "access_token_validity": "minutes=5",
        "refresh_token_validity": "days=1",
    },
)
provider.redirect_uris = [
    RedirectURI(RedirectURIMatchingMode.STRICT, CFG["redirect_uri"])  # noqa: F821
]
provider.save()
provider.property_mappings.set(
    ScopeMapping.objects.filter(
        managed__in=[f"goauthentik.io/providers/oauth2/scope-{s}" for s in MANAGED_SCOPES]
    )
)
print("STATUS provider", "created" if created else "updated", provider.name)

application, created = Application.objects.update_or_create(
    slug=CFG["slug"],  # noqa: F821
    defaults={
        "name": CFG["name"],  # noqa: F821
        "provider": provider,
        "meta_launch_url": CFG["launch_url"],  # noqa: F821
        "open_in_new_tab": True,
        "meta_icon": "fa://fa-gauge-high",
        "policy_engine_mode": "any",
        "group": CFG.get("category", ""),  # noqa: F821
    },
)
print("STATUS application", "created" if created else "updated", application.slug)

wanted = []
for name in CFG["admin_groups"] + CFG["user_groups"]:  # noqa: F821
    group, created = Group.objects.get_or_create(name=name)
    wanted.append(group)
    print("STATUS group", "created" if created else "exists", name)
for binding in PolicyBinding.objects.filter(target=application):
    if binding.group not in wanted:
        print("STATUS binding removed", binding.group.name if binding.group else binding.pk)
        binding.delete()
for order, group in enumerate(wanted):
    PolicyBinding.objects.update_or_create(
        target=application,
        group=group,
        defaults={"negate": False, "enabled": True, "order": order, "timeout": 30},
    )
print("STATUS bound", [g.name for g in wanted])
