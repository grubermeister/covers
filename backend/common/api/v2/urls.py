###################################################################################################
## WoCo Commons - API v2 Routing (Phase 2 rewrite)
##
## Unified routes per docs/model.md: markings is polymorphic across
## TOWNMARK/RATEMARK/AUXMARK; images is polymorphic; cover-dates and
## cover-valuations are cover-scoped.
###################################################################################################
from django.urls import include, path
from django.views.decorators.csrf import csrf_exempt
from rest_framework.routers import DefaultRouter

from common.api.auth import (
    ChangePasswordApiView,
    CurrentUserView,
    ForgotPasswordApiView,
    LoginRequestView,
    LoginView,
    LogoutView,
    ResetPasswordApiView,
)
from common.api.help import HelpDocsView

from . import views


router = DefaultRouter()

# Geography and lookups
router.register(r"regions", views.RegionViewSet, basename="region")
router.register(r"post-offices", views.PostOfficeViewSet, basename="post-office")
router.register(r"letterings", views.LetteringViewSet, basename="lettering")
router.register(r"shapes", views.ShapeViewSet, basename="shape")
router.register(r"colors", views.ColorViewSet, basename="color")
router.register(r"reference-works", views.ReferenceWorkViewSet, basename="reference-work")
router.register(r"citations", views.CitationViewSet, basename="citation")
router.register(r"faq-entries", views.FAQEntryViewSet, basename="faq-entry")

# Catalog (unified marking + polymorphic image, cover-side observations)
router.register(r"markings", views.MarkingViewSet, basename="marking")
router.register(r"images", views.ImageViewSet, basename="image")
router.register(r"covers", views.CoverV2ViewSet, basename="cover-v2")
router.register(r"cover-markings", views.CoverMarkingViewSet, basename="cover-marking")
router.register(r"dates-seen", views.DateSeenViewSet, basename="dates-seen")
router.register(r"cover-valuations", views.CoverValuationViewSet, basename="cover-valuation")

# Moderation, governance, admin
router.register(r"contributions", views.ContributionViewSet, basename="contribution")
router.register(r"collections", views.CollectionViewSet, basename="collection")

urlpatterns = [
    # Auth
    path("login/", csrf_exempt(LoginView.as_view()), name="login"),
    path("logout/", csrf_exempt(LogoutView.as_view()), name="logout"),
    path("me/", CurrentUserView.as_view(), name="current-user"),
    path("login-requests/", csrf_exempt(LoginRequestView.as_view()), name="login-request"),
    path("forgot-password/", csrf_exempt(ForgotPasswordApiView.as_view()), name="forgot-password"),
    path("reset-password/", csrf_exempt(ResetPasswordApiView.as_view()), name="reset-password"),
    path("change-password/", csrf_exempt(ChangePasswordApiView.as_view()), name="change-password"),
    path("help-docs/", HelpDocsView.as_view(), name="help-docs"),

    # Custom non-router endpoints.
    # Marking removal/restore are router-generated detail actions on
    # MarkingViewSet (POST /markings/<pk>/remove/ and .../restore/), not
    # standalone paths. The old DELETE /markings/<pk>/delete-mine/ endpoint
    # was retired in favor of the recycle-bin flow.
    path(
        "markings-range/",
        views.MarkingDateRangeView.as_view(),
        name="markings-range",
    ),
    path(
        "catalog-code-suggestions/",
        views.CatalogCodeSuggestionView.as_view(),
        name="catalog-code-suggestions",
    ),

    path("", include(router.urls)),
]

###################################################################################################
