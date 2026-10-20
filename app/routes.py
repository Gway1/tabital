from flask import current_app
from flask_praetorian.exceptions import PraetorianError
from flask_restful import Api

from .docs import init_docs

# Admin Resources
from .resources.admin import (
    PendingUsersResource,
    ApproveUserResource,
    RejectUserResource,
    GetCustomersResource,
    CustomerResource,
    GetMerchantsResource,
    MerchantResource,
    VerifyMerchantResource,
    MerchantStatsResource,
    BulkUpdateCustomersResource,
    SearchCustomersResource,
    CustomerStatsResource,
    ExportCustomersResource,
    MerchantKYCResource,
    MerchantCommissionResource,
    MerchantSettlementResource,
    GetCurrentUserResource,
    AdminApproveOrderResource,
)

from app.resources.admin_users import (
    AdminUserStatsResource,
    AdminGetAllUsersResource,
    AdminGetUserDetailResource,
    AdminUpdateUserStatusResource,
    AdminDeleteUserResource,
    AdminExportUsersResource,
)

from app.resources.admin_product_plans import (
    AdminProductStatsResource,
    AdminGetProductsResource,
    AdminGetProductDetailResource,
    AdminUpdateProductStatusResource,
    AdminUpdateProductFeaturedResource,
    AdminUpdateProductStockResource,
    AdminExportProductsResource,
)

# Product Plans Routes

# Transaction Resources
from .resources.transaction import (
    GetTransactionsResource,
    CreateTransactionResource,
    MerchantPayoutStatsResource,
    MerchantRecentPayoutsResource,
    UpdateTransactionStatusResource,
    GetTransactionStatsResource,
    DeleteTransactionResource,
    MerchantGetTransactionsResource,
    MerchantGetTransactionStatsResource,
    MerchantUpdateTransactionStatusResource,
    MerchantUpdateTransactionResource,
    MerchantRefundTransactionResource,
    MerchantExportTransactionsResource,
)

# Document Resources
from .resources.document import (
    GetMerchantDocumentsResource,
    UploadDocumentResource,
    VerifyDocumentResource,
    DeleteDocumentResource,
)


from .services.auth_service import (
    ForgotPasswordResource,
    VerifyResetOTPResource,
    ResetPasswordResource,
)


# Auth Resources
from .resources.auth import CheckUserExistsResource, RegisterResource, LoginResource

# Protected Resources
from .resources.protected import ProtectedResource

# Admin Stats Resources
from .resources.admin_stats import AdminStatsResource

# Merchant Dashboard Resources
from .resources.merchant_dashboard import (
    MerchantDashboardStatsResource,
    MerchantSalesChartResource,
    MerchantRecentTransactionsResource,
    MerchantInstalmentsOverviewResource,
    MerchantSettlementInfoResource,
    MerchantAccountStatusResource,
    MerchantQuickActionsResource,
)

# Merchant Customers Resources
from .resources.merchant_customers import (
    MerchantGetCustomersResource,
    MerchantGetCustomerDetailsResource,
    MerchantUpdateCustomerResource,
    MerchantGetCustomerStatsResource,
    MerchantExportCustomersResource,
)

# Merchant Settlements Resources
from .resources.merchant_settlements import (
    MerchantGetSettlementsResource,
    MerchantGetSettlementSummaryResource,
    MerchantRequestPayoutResource,
    MerchantUpdateSettlementSettingsResource,
    MerchantGetSettlementDetailsResource,
)

# Merchant Disputes Resources
from .resources.merchant_disputes import (
    MerchantGetDisputesResource,
    MerchantGetDisputeStatsResource,
    MerchantGetDisputeDetailsResource,
    MerchantUpdateDisputeResource,
    MerchantAcceptDisputeResource,
    MerchantRejectDisputeResource,
    MerchantEscalateDisputeResource,
)

# Add to your route registration
from .resources.admin_dashboard import (
    AdminDashboardStatsResource,
    AdminRecentTransactionsResource,
)


# Merchant Reports Resources
from .resources.merchant_reports import (
    MerchantSalesReportResource,
    MerchantTransactionReportResource,
    MerchantCustomerReportResource,
    MerchantFinancialReportResource,
    MerchantInstalmentReportResource,
    MerchantExportReportResource,
)

# Add to your route registration
from .resources.admin_customers import (
    AdminCustomerStatsResource,
    AdminGetCustomersResource,
    AdminGetCustomerDetailResource,
    AdminUpdateCustomerStatusResource,
    AdminAddCustomerNoteResource,
)

# In your routes.py or app.py file

from .resources.admin_merchants import (
    AdminMerchantStatsResource,
    AdminGetMerchantsResource,
    AdminGetMerchantDetailResource,
    AdminUpdateMerchantStatusResource,
    AdminUpdateMerchantCommissionResource,
    AdminAdjustMerchantReserveResource,
)

# ============================================
# ADMIN MERCHANT MANAGEMENT ROUTES
# ============================================


# Merchant Settings Resources
from .resources.merchant_settings import (
    MerchantGetProfileResource,
    MerchantUpdateProfileResource,
    MerchantUpdatePasswordResource,
    MerchantUpdatePaymentSettingsResource,
    MerchantGetNotificationSettingsResource,
    MerchantUpdateNotificationSettingsResource,
    MerchantGetPreferencesResource,
    MerchantUpdatePreferencesResource,
    MerchantUpdateKYCResource,
    MerchantUploadDocumentResource,
    MerchantGetActivityLogResource,
)

# Customer Resources
from .resources.customer_dashboard import (
    CustomerDashboardStatsResource,
    CustomerPaymentOverviewResource,
    CustomerUpcomingPaymentsResource,
    CustomerRecentTransactionsResource,
    CustomerInstalmentsResource,
    CustomerPlanDetailsResource,
)

from .resources.customer_profile import (
    CustomerGetProfileResource,
    CustomerUpdateProfileResource,
    CustomerUpdatePasswordResource,
    CustomerGetKYCStatusResource,
)
from .resources.admin_settlements import (
    AdminSettlementStatsResource,
    AdminGetSettlementsResource,
    AdminGetSettlementDetailResource,
    AdminProcessSettlementResource,
    AdminProcessSingleSettlementResource,
    AdminExportSettlementsResource,
)
from app.resources.admin_reports import (
    AdminReportRevenueResource,
    AdminReportTransactionsResource,
    AdminReportCustomersResource,
    AdminReportMerchantsResource,
    AdminReportInstalmentsResource,
    AdminReportDownloadResource,
    AdminDashboardKPIResource,
)

# Reports & Analytics Routes

from .resources.customer_notifications import (
    CustomerGetNotificationsResource,
    CustomerMarkNotificationReadResource,
    CustomerMarkAllNotificationsReadResource,
    CustomerGetNotificationSettingsResource,
    CustomerUpdateNotificationSettingsResource,
    CustomerUnreadNotificationCountResource,
)

# routes.py - Add these imports

from .resources.customer_payments import (
    CustomerGetPaymentsResource,
    CustomerGetPaymentStatsResource,
    CustomerDownloadReceiptResource,
    CustomerPaidPaymentsResource,
    CustomerPaymentReminderResource,
    CustomerMakePaymentResource,
)

from .resources.customer_transactions import (
    CustomerGetTransactionsResource,
    CustomerGetTransactionDetailsResource,
    CustomerGetTransactionStatsResource,
    CustomerExportTransactionsResource,
)

from .resources.customer_support import (
    CustomerCreateTicketResource,
    CustomerGetTicketsResource,
    CustomerGetTicketDetailsResource,
    CustomerAddTicketMessageResource,
    CustomerCloseTicketResource,
)

# System Settings Resources
from .resources.system_settings import (
    SystemSettingsResource,
    InstallmentCalculatorResource,
    LateFeeCalculatorResource,
)

from .resources.system_settings import (
    SystemSettingsResource,
    InstallmentOptionsResource,  # Add this
    InstallmentCalculatorResource,
    LateFeeCalculatorResource,
)

from .resources.product import MerchantProductsResource, MerchantProductDetailResource

from .resources.admin_orders import AdminGetOrdersResource, AdminRejectOrderResource
from .resources.merchant_orders import (
    MerchantGetOrdersResource,
    MerchantUpdateDeliveryStatusResource,
)
from .resources.customer_purchase import (
    CustomerPurchaseResource,
    CustomerGetOrdersResource,
    CustomerPayOrderResource,
)

# routes.py - Add these imports
from .resources.customer_product import (
    CustomerGetProductsResource,
    CustomerGetProductDetailsResource,
    CustomerGetProductCategoriesResource,
)
from .resources.customer_payments import (
    CustomerGetPaymentsResource,
    CustomerGetPaymentStatsResource,
    CustomerDownloadReceiptResource,
)

# Add to routes.py

# routes.py - Add these imports

from .resources.customer_document import (
    CustomerGetDocumentsResource,
    CustomerUploadDocumentsResource,
    CustomerGetKYCStatusResource,
)

# routes.py - Add these imports and routes

from .resources.merchant_document import (
    MerchantGetDocumentsResource,
    MerchantUploadDocumentsResource,
    MerchantGetKYCStatusResource,
    MerchantGetBankDetailsResource,
    MerchantUpdateBankDetailsResource,
    AdminGetPendingMerchantKYCResource,
    AdminVerifyMerchantDocumentResource,
    AdminVerifyMerchantCompleteResource,
)

from .resources.notification import (
    GetNotificationsResource,
    MarkNotificationReadResource,
    MarkAllNotificationsReadResource,
    DeleteNotificationResource,
    ClearAllNotificationsResource,
    GetUnreadCountResource,
)

# routes.py - Add these imports

from .resources.merchant_notifications import (
    MerchantGetNotificationsResource,
    MerchantMarkNotificationReadResource,
    MerchantMarkAllNotificationsReadResource,
    MerchantGetNotificationSettingsResource,
    MerchantUpdateNotificationSettingsResource,
    MerchantUnreadNotificationCountResource,
    MerchantDeleteNotificationResource,
    MerchantClearAllNotificationsResource,
)
from .resources.admin_kyc import (
    AdminGetPendingKYCResource,
    AdminGetVerifiedKYCResource,
    AdminGetRejectedKYCResource,
    AdminGetMerchantKYCResource,
    AdminApproveKYCResource,
    AdminRejectKYCResource,
    AdminApproveDocumentResource,
    AdminRejectDocumentResource,
)
from .resources.customer_document import (
    AdminGetPendingCustomerKYCResource,
    AdminGetVerifiedCustomerKYCResource,
    AdminGetRejectedCustomerKYCResource,
    AdminApproveCustomerKYCResource,
    AdminRejectCustomerKYCResource,
    AdminApproveCustomerDocumentResource,
    AdminRejectCustomerDocumentResource,
)

from app.resources.admin_transactions import (
    AdminTransactionStatsResource,
    AdminGetTransactionsResource,
    AdminGetTransactionDetailResource,
    AdminUpdateTransactionStatusResource,
    AdminUpdateDeliveryStatusResource,
    AdminRefundTransactionResource,
    AdminExportTransactionsResource,
)

from app.resources.admin_instalments import (
    AdminInstalmentStatsResource,
    AdminGetInstalmentPlansResource,
    AdminGetInstalmentPlanDetailResource,
    AdminUpdateInstalmentStatusResource,
    AdminApplyLateFeeResource,
    AdminWaiveLateFeeResource,
    AdminMarkPaymentAsPaidResource,
    AdminExportInstalmentsResource,
)

from app.resources.admin_collection import (
    AdminCollectionStatsResource,
    AdminGetOverduePaymentsResource,
    AdminGetOverduePaymentDetailResource,
    AdminSendPaymentReminderResource,
    AdminMarkPaymentReceivedResource,
    AdminSetPaymentPlanResource,
    AdminExportOverduePaymentsResource,
)

# Instalment Management Routes


# In routes.py
# from .resources.customer_document import CustomerUploadOptionalDocumentResource

# In register_routes function, add:

# ============================================
# MERCHANT NOTIFICATION ROUTES
# ============================================


# In register_routes function, add:

# ============================================
# MERCHANT DOCUMENT ROUTES
# ============================================

# Add to register_routes function:

# ============================================
# CUSTOMER DOCUMENT ROUTES
# ============================================

# Add to register_routes function:

# Add to register_routes function:
# ============================================
# CUSTOMER PRODUCT ROUTES
# ============================================


from .resources.paystack_payments import (
    CustomerPaymentConfigResource,
    CustomerPaystackInitializeResource,
    CustomerPaystackVerifyResource,
    PaystackWebhookResource,
)


from .resources.underwriting import (
    CustomerCreditResource,
    CustomerUnderwritingDetailsResource,
    AdminCustomerUnderwritingResource,
    AdminRerunUnderwritingResource,
    AdminCreditLimitOverrideResource,
)


from .resources.servicing import (
    CustomerPaymentMethodsResource,
    CustomerPaymentMethodResource,
    CustomerDisputesResource,
    AdminDisputesResource,
    AdminResolveDisputeResource,
    AdminRunServicingResource,
    AdminMessagesResource,
)


from .resources.merchant_payouts import (
    MerchantPayoutAccountResource,
    MerchantPayoutBanksResource,
    MerchantSettlementBatchesResource,
    MerchantSettlementBatchResource,
    MerchantStatementResource,
    AdminSettlementBatchesResource,
    AdminGenerateSettlementsResource,
    AdminSettlementBatchResource,
    AdminApproveSettlementResource,
    MerchantPaymentLinksResource,
    MerchantPaymentLinkResource,
    CustomerPaymentLinkResource,
)
from .resources.identity_fraud import (
    CustomerIdentityResource,
    CustomerIdentityStartResource,
    CustomerIdentitySubmittedResource,
    SmileIDWebhookResource,
    AdminIdentityChecksResource,
    AdminIdentityDecideResource,
    AdminEmploymentVerificationResource,
    AdminFraudSignalsResource,
    AdminFraudSignalResource,
)


from .resources.deferments import CustomerDefermentResource, AdminDefermentsResource


from .resources.economics import (
    AdminEconomicsSummaryResource,
    AdminEconomicsExportResource,
    AdminEconomicsPortfolioResource,
    AdminEconomicsCohortsResource,
    AdminEconomicsScenarioResource,
)


from .resources.business_settings import (
    AdminBusinessSettingsResource,
    AdminBusinessSettingsHistoryResource,
)


from .resources.customer_product import CustomerPlanOptionsResource
from .resources.pii import AdminPiiRevealResource, AdminPiiAccessLogResource


class TabitalApi(Api):
    """flask_restful turns every exception into a 500 unless it carries a `code`, which hides the
    401/403 that flask_praetorian raises for a missing, bad or expired token. Hand those back to the
    handler flask_praetorian registered on the app."""

    def handle_error(self, e):
        if isinstance(e, PraetorianError):
            return current_app.handle_user_exception(e)
        return super().handle_error(e)


def register_routes(app):
    api = TabitalApi(app)

    # Vault shop: every plan for a product, priced by the server, to compare side by side
    api.add_resource(CustomerPlanOptionsResource, "/customer/plan-options")

    # Masked personal data on admin screens: reveal one value with a reason (logged)
    from .resources.payment_claims import (
        AdminPaymentClaimsResource,
        AdminPaymentClaimConfirmResource,
        AdminPaymentClaimRejectResource,
    )

    api.add_resource(AdminPaymentClaimsResource, "/admin/payment-claims")
    api.add_resource(
        AdminPaymentClaimConfirmResource, "/admin/payment-claims/<int:claim_id>/confirm"
    )
    api.add_resource(
        AdminPaymentClaimRejectResource, "/admin/payment-claims/<int:claim_id>/reject"
    )

    from .resources.merchant_fee_tiers import (
        AdminMerchantFeeTiersResource,
        AdminMerchantFeeTierResource,
    )

    api.add_resource(AdminMerchantFeeTiersResource, "/admin/merchant-fee-tiers")
    api.add_resource(
        AdminMerchantFeeTierResource, "/admin/merchants/<int:merchant_id>/fee-tier"
    )
    from .resources.admin_team import AdminTeamResource, AdminTeamMemberResource

    api.add_resource(AdminTeamResource, "/admin/team")
    api.add_resource(AdminTeamMemberResource, "/admin/team/<int:admin_id>")
    api.add_resource(AdminPiiRevealResource, "/admin/pii/reveal")
    api.add_resource(AdminPiiAccessLogResource, "/admin/pii/access-log")

    # Business settings: validated, audited (replaces the broken /api/system-settings page)
    api.add_resource(AdminBusinessSettingsResource, "/admin/business-settings")
    api.add_resource(
        AdminBusinessSettingsHistoryResource, "/admin/business-settings/history"
    )

    # Unit economics and portfolio reporting (Phase 7)
    api.add_resource(AdminEconomicsSummaryResource, "/admin/economics/summary")
    api.add_resource(AdminEconomicsExportResource, "/admin/economics/export")
    api.add_resource(AdminEconomicsPortfolioResource, "/admin/economics/portfolio")
    api.add_resource(AdminEconomicsCohortsResource, "/admin/economics/cohorts")
    api.add_resource(AdminEconomicsScenarioResource, "/admin/economics/scenario")

    # Instalment deferment (§4)
    api.add_resource(
        CustomerDefermentResource, "/customer/plans/<int:plan_id>/deferment"
    )
    api.add_resource(AdminDefermentsResource, "/admin/deferments")

    # ============================================
    # IDENTITY (Smile ID) AND FRAUD CHECKS (Phase 6)
    # ============================================
    api.add_resource(CustomerIdentityResource, "/customer/identity")
    api.add_resource(CustomerIdentityStartResource, "/customer/identity/start")
    api.add_resource(
        CustomerIdentitySubmittedResource, "/customer/identity/<int:check_id>/submitted"
    )
    api.add_resource(SmileIDWebhookResource, "/webhooks/smileid")
    api.add_resource(AdminIdentityChecksResource, "/admin/identity-checks")
    api.add_resource(
        AdminIdentityDecideResource, "/admin/identity-checks/<int:check_id>/decide"
    )
    api.add_resource(
        AdminEmploymentVerificationResource,
        "/admin/customers/<int:customer_id>/employment-verification",
    )
    api.add_resource(AdminFraudSignalsResource, "/admin/fraud-signals")
    api.add_resource(AdminFraudSignalResource, "/admin/fraud-signals/<int:signal_id>")

    # ============================================
    # MERCHANT SETTLEMENTS, PAYOUTS, PAYMENT LINKS (Phase 5)
    # ============================================
    api.add_resource(MerchantPayoutAccountResource, "/merchant/payout-account")
    api.add_resource(MerchantPayoutBanksResource, "/merchant/payout-banks")
    api.add_resource(MerchantSettlementBatchesResource, "/merchant/settlement-batches")
    api.add_resource(
        MerchantSettlementBatchResource, "/merchant/settlement-batches/<int:batch_id>"
    )
    api.add_resource(MerchantStatementResource, "/merchant/statement")
    api.add_resource(AdminSettlementBatchesResource, "/admin/settlement-batches")
    api.add_resource(
        AdminGenerateSettlementsResource, "/admin/settlement-batches/generate"
    )
    api.add_resource(
        AdminSettlementBatchResource, "/admin/settlement-batches/<int:batch_id>"
    )
    api.add_resource(
        AdminApproveSettlementResource,
        "/admin/settlement-batches/<int:batch_id>/approve",
    )
    api.add_resource(MerchantPaymentLinksResource, "/merchant/payment-links")
    api.add_resource(
        MerchantPaymentLinkResource, "/merchant/payment-links/<int:link_id>"
    )
    api.add_resource(
        CustomerPaymentLinkResource, "/customer/payment-links/<string:token>"
    )

    # ============================================
    # SERVICING: autopay cards, disputes, daily job (Phase 4)
    # ============================================
    api.add_resource(CustomerPaymentMethodsResource, "/customer/payment-methods")
    api.add_resource(
        CustomerPaymentMethodResource, "/customer/payment-methods/<int:method_id>"
    )
    api.add_resource(CustomerDisputesResource, "/customer/disputes")
    api.add_resource(AdminDisputesResource, "/admin/disputes")
    api.add_resource(
        AdminResolveDisputeResource, "/admin/disputes/<int:dispute_id>/resolve"
    )
    api.add_resource(AdminRunServicingResource, "/admin/servicing/run")
    api.add_resource(AdminMessagesResource, "/admin/messages")

    # ============================================
    # UNDERWRITING (Phase 3)
    # ============================================
    api.add_resource(CustomerCreditResource, "/customer/credit")
    api.add_resource(CustomerUnderwritingDetailsResource, "/customer/underwriting")
    api.add_resource(
        AdminCustomerUnderwritingResource,
        "/admin/customers/<int:customer_id>/underwriting",
    )
    api.add_resource(
        AdminRerunUnderwritingResource,
        "/admin/customers/<int:customer_id>/underwriting/rerun",
    )

    # ============================================
    # PAYSTACK (Phase 2)
    # ============================================
    api.add_resource(CustomerPaymentConfigResource, "/customer/payments/config")
    api.add_resource(
        CustomerPaystackInitializeResource, "/customer/payments/paystack/initialize"
    )
    api.add_resource(
        CustomerPaystackVerifyResource,
        "/customer/payments/paystack/verify/<string:reference>",
    )
    api.add_resource(PaystackWebhookResource, "/webhooks/paystack")

    # ============================================
    # AUTHENTICATION ROUTES
    # ============================================
    api.add_resource(RegisterResource, "/register")
    api.add_resource(LoginResource, "/login")

    # ============================================
    # ADMIN - USER MANAGEMENT ROUTES
    # ============================================
    api.add_resource(PendingUsersResource, "/admin/pending-users")
    api.add_resource(ApproveUserResource, "/admin/approve/<int:user_id>")
    api.add_resource(RejectUserResource, "/admin/reject/<int:user_id>")
    api.add_resource(AdminStatsResource, "/admin/stats")

    # ============================================
    # ADMIN - CUSTOMER ROUTES
    # ============================================
    # api.add_resource(GetCustomersResource, "/admin/customers")   # old duplicate: shadowed the current endpoint (admin_customers/admin_merchants)
    api.add_resource(CustomerResource, "/admin/customers/<int:customer_id>")
    api.add_resource(BulkUpdateCustomersResource, "/admin/customers/bulk-update")
    api.add_resource(SearchCustomersResource, "/admin/customers/search")
    # api.add_resource(CustomerStatsResource, "/admin/customers/stats")   # old duplicate: shadowed the current endpoint (admin_customers/admin_merchants)
    api.add_resource(ExportCustomersResource, "/admin/customers/export")

    # ============================================
    # ADMIN - MERCHANT ROUTES
    # ============================================
    # api.add_resource(GetMerchantsResource, "/admin/merchants")   # old duplicate: shadowed the current endpoint (admin_customers/admin_merchants)
    api.add_resource(MerchantResource, "/admin/merchants/<int:merchant_id>")
    api.add_resource(
        VerifyMerchantResource, "/admin/merchants/verify/<int:merchant_id>"
    )
    # api.add_resource(MerchantStatsResource, "/admin/merchants/stats")   # old duplicate: shadowed the current endpoint (admin_customers/admin_merchants)

    api.add_resource(MerchantKYCResource, "/admin/merchants/<int:merchant_id>/kyc")
    api.add_resource(
        MerchantCommissionResource, "/admin/merchants/<int:merchant_id>/commission"
    )
    api.add_resource(
        MerchantSettlementResource, "/admin/merchants/<int:merchant_id>/settlement"
    )

    # ============================================
    # ADMIN - DOCUMENT ROUTES
    # ============================================
    api.add_resource(
        GetMerchantDocumentsResource, "/admin/merchants/<int:merchant_id>/documents"
    )
    api.add_resource(
        UploadDocumentResource, "/admin/merchants/<int:merchant_id>/documents/upload"
    )
    api.add_resource(
        VerifyDocumentResource, "/admin/documents/<int:document_id>/verify"
    )
    api.add_resource(DeleteDocumentResource, "/admin/documents/<int:document_id>")

    # ============================================
    # ADMIN - SYSTEM SETTINGS ROUTES
    # ============================================
    api.add_resource(SystemSettingsResource, "/admin/settings/charges")
    api.add_resource(InstallmentCalculatorResource, "/installment/calculate")
    api.add_resource(LateFeeCalculatorResource, "/installment/late-fee")

    # ============================================
    # TRANSACTION ROUTES
    # ============================================
    api.add_resource(GetTransactionsResource, "/transactions")
    api.add_resource(CreateTransactionResource, "/transactions/create")
    api.add_resource(
        UpdateTransactionStatusResource, "/transactions/<int:transaction_id>/status"
    )
    api.add_resource(GetTransactionStatsResource, "/transactions/stats")
    api.add_resource(DeleteTransactionResource, "/transactions/<int:transaction_id>")

    # ============================================
    # CURRENT USER ROUTE
    # ============================================
    api.add_resource(GetCurrentUserResource, "/admin/get_current_user")

    # ============================================
    # MERCHANT DASHBOARD ROUTES
    # ============================================
    api.add_resource(MerchantDashboardStatsResource, "/merchant/dashboard/stats")
    api.add_resource(MerchantSalesChartResource, "/merchant/dashboard/sales-chart")
    api.add_resource(
        MerchantRecentTransactionsResource, "/merchant/transactions/recent"
    )
    api.add_resource(
        MerchantInstalmentsOverviewResource, "/merchant/instalments/overview"
    )
    api.add_resource(MerchantSettlementInfoResource, "/merchant/settlements/info")
    api.add_resource(MerchantAccountStatusResource, "/merchant/account/status")
    api.add_resource(MerchantQuickActionsResource, "/merchant/quick-actions")

    # ============================================
    # MERCHANT TRANSACTION ROUTES
    # ============================================
    api.add_resource(MerchantGetTransactionsResource, "/merchant/transactions")
    api.add_resource(
        MerchantGetTransactionStatsResource, "/merchant/transactions/stats"
    )
    api.add_resource(
        MerchantUpdateTransactionStatusResource,
        "/merchant/transactions/<int:transaction_id>/status",
    )
    api.add_resource(
        MerchantUpdateTransactionResource, "/merchant/transactions/<int:transaction_id>"
    )
    api.add_resource(
        MerchantRefundTransactionResource,
        "/merchant/transactions/<int:transaction_id>/refund",
    )
    api.add_resource(
        MerchantExportTransactionsResource, "/merchant/transactions/export"
    )

    # ============================================
    # MERCHANT CUSTOMER ROUTES
    # ============================================
    api.add_resource(MerchantGetCustomersResource, "/merchant/customers")
    api.add_resource(
        MerchantGetCustomerDetailsResource, "/merchant/customers/<int:customer_id>"
    )
    api.add_resource(
        MerchantUpdateCustomerResource, "/merchant/customers/<int:customer_id>"
    )
    api.add_resource(MerchantGetCustomerStatsResource, "/merchant/customers/stats")
    api.add_resource(MerchantExportCustomersResource, "/merchant/customers/export")

    # ============================================
    # MERCHANT SETTLEMENT ROUTES
    # ============================================
    api.add_resource(MerchantGetSettlementsResource, "/merchant/settlements")
    api.add_resource(
        MerchantGetSettlementSummaryResource, "/merchant/settlements/summary"
    )
    api.add_resource(
        MerchantRequestPayoutResource, "/merchant/settlements/request-payout"
    )
    api.add_resource(
        MerchantUpdateSettlementSettingsResource, "/merchant/settlements/settings"
    )
    api.add_resource(
        MerchantGetSettlementDetailsResource,
        "/merchant/settlements/<string:settlement_id>",
    )

    # ============================================
    # MERCHANT DISPUTE ROUTES
    # ============================================
    api.add_resource(MerchantGetDisputesResource, "/merchant/disputes")
    api.add_resource(MerchantGetDisputeStatsResource, "/merchant/disputes/stats")
    api.add_resource(
        MerchantGetDisputeDetailsResource, "/merchant/disputes/<int:dispute_id>"
    )
    api.add_resource(
        MerchantUpdateDisputeResource, "/merchant/disputes/<int:dispute_id>"
    )
    api.add_resource(
        MerchantAcceptDisputeResource, "/merchant/disputes/<int:dispute_id>/accept"
    )
    api.add_resource(
        MerchantRejectDisputeResource, "/merchant/disputes/<int:dispute_id>/reject"
    )
    api.add_resource(
        MerchantEscalateDisputeResource, "/merchant/disputes/<int:dispute_id>/escalate"
    )

    # ============================================
    # MERCHANT REPORT ROUTES
    # ============================================
    api.add_resource(MerchantSalesReportResource, "/merchant/reports/sales")
    api.add_resource(
        MerchantTransactionReportResource, "/merchant/reports/transactions"
    )
    api.add_resource(MerchantCustomerReportResource, "/merchant/reports/customers")
    api.add_resource(MerchantFinancialReportResource, "/merchant/reports/financial")
    api.add_resource(MerchantInstalmentReportResource, "/merchant/reports/instalments")
    api.add_resource(MerchantExportReportResource, "/merchant/reports/export")

    # ============================================
    # MERCHANT SETTINGS ROUTES
    # ============================================
    api.add_resource(MerchantGetProfileResource, "/merchant/settings/profile")
    api.add_resource(MerchantUpdateProfileResource, "/merchant/settings/profile")
    api.add_resource(MerchantUpdatePasswordResource, "/merchant/settings/password")
    api.add_resource(
        MerchantUpdatePaymentSettingsResource, "/merchant/settings/payment"
    )
    api.add_resource(
        MerchantGetNotificationSettingsResource, "/merchant/settings/notifications"
    )
    api.add_resource(
        MerchantUpdateNotificationSettingsResource, "/merchant/settings/notifications"
    )
    api.add_resource(MerchantGetPreferencesResource, "/merchant/settings/preferences")
    api.add_resource(
        MerchantUpdatePreferencesResource, "/merchant/settings/preferences"
    )
    api.add_resource(MerchantUpdateKYCResource, "/merchant/settings/kyc")
    api.add_resource(
        MerchantUploadDocumentResource, "/merchant/settings/upload-document"
    )
    api.add_resource(MerchantGetActivityLogResource, "/merchant/settings/activity-log")

    # ============================================
    # CUSTOMER DASHBOARD ROUTES
    # ============================================
    api.add_resource(CustomerDashboardStatsResource, "/customer/dashboard/stats")
    api.add_resource(CustomerPaymentOverviewResource, "/customer/payment-overview")
    api.add_resource(CustomerUpcomingPaymentsResource, "/customer/upcoming-payments")
    api.add_resource(
        CustomerRecentTransactionsResource, "/customer/recent-transactions"
    )
    api.add_resource(CustomerInstalmentsResource, "/customer/instalments")
    api.add_resource(CustomerPlanDetailsResource, "/customer/instalments/<int:plan_id>")
    api.add_resource(CustomerMakePaymentResource, "/customer/payments/make")

    # ============================================
    # CUSTOMER PROFILE ROUTES
    # ============================================
    api.add_resource(CustomerGetProfileResource, "/customer/profile")
    api.add_resource(CustomerUpdateProfileResource, "/customer/profile")
    api.add_resource(CustomerUpdatePasswordResource, "/customer/password")
    api.add_resource(CustomerGetKYCStatusResource, "/customer/kyc/status")
    api.add_resource(CustomerUploadDocumentsResource, "/customer/documents/upload")
    # api.add_resource(CustomerGetActivityLogResource, "/customer/settings/activity-log")

    # ============================================
    # CUSTOMER NOTIFICATION ROUTES
    # ============================================
    api.add_resource(CustomerGetNotificationsResource, "/customer/notifications")
    api.add_resource(
        CustomerMarkNotificationReadResource,
        "/customer/notifications/<string:notification_id>/read",
    )
    api.add_resource(
        CustomerMarkAllNotificationsReadResource,
        "/customer/notifications/mark-all-read",
    )
    api.add_resource(
        CustomerGetNotificationSettingsResource, "/customer/notifications/settings"
    )
    api.add_resource(
        CustomerUpdateNotificationSettingsResource, "/customer/notifications/settings"
    )
    api.add_resource(
        CustomerUnreadNotificationCountResource, "/customer/notifications/unread-count"
    )

    # ============================================
    # CUSTOMER PAYMENT ROUTES
    # ============================================
    api.add_resource(CustomerPaymentReminderResource, "/customer/payments/reminder")

    # ============================================
    # CUSTOMER TRANSACTION ROUTES
    # ============================================
    api.add_resource(CustomerGetTransactionsResource, "/customer/transactions")
    api.add_resource(
        CustomerGetTransactionDetailsResource,
        "/customer/transactions/<int:transaction_id>",
    )
    api.add_resource(
        CustomerGetTransactionStatsResource, "/customer/transactions/stats"
    )
    api.add_resource(
        CustomerExportTransactionsResource, "/customer/transactions/export"
    )

    # ============================================
    # CUSTOMER SUPPORT ROUTES
    # ============================================
    api.add_resource(CustomerCreateTicketResource, "/customer/support/ticket")
    api.add_resource(CustomerGetTicketsResource, "/customer/support/tickets")
    api.add_resource(
        CustomerGetTicketDetailsResource, "/customer/support/tickets/<int:ticket_id>"
    )
    api.add_resource(
        CustomerAddTicketMessageResource,
        "/customer/support/tickets/<int:ticket_id>/reply",
    )
    api.add_resource(
        CustomerCloseTicketResource, "/customer/support/tickets/<int:ticket_id>/close"
    )

    # ============================================
    # PROTECTED ROUTES
    # ============================================

    # Add to imports in routes.py

    # Add to register_routes function:
    # api.add_resource(SystemSettingsResource, "/admin/settings/charges")
    api.add_resource(
        InstallmentOptionsResource, "/admin/settings/installments"
    )  # Add this
    # api.add_resource(InstallmentCalculatorResource, "/installment/calculate")
    # api.add_resource(LateFeeCalculatorResource, "/installment/late-fee")

    # resources/__init__.py - Add product routes

    # Add to register_routes function:
    api.add_resource(MerchantProductsResource, "/merchant/products")
    api.add_resource(
        MerchantProductDetailResource, "/merchant/products/<int:product_id>"
    )
    api.add_resource(ProtectedResource, "/protected")

    # api.add_resource(CustomerUploadOptionalDocumentResource, "/customer/documents/upload-optional")
    # Add to routes.py imports

    # Add to register_routes function:
    # ============================================
    # ADMIN ORDER ROUTES
    # ============================================
    api.add_resource(AdminGetOrdersResource, "/admin/orders")
    api.add_resource(AdminApproveOrderResource, "/admin/orders/<int:order_id>/approve")
    api.add_resource(AdminRejectOrderResource, "/admin/orders/<int:order_id>/reject")

    # ============================================
    # MERCHANT ORDER ROUTES
    # ============================================
    api.add_resource(MerchantGetOrdersResource, "/merchant/orders")
    api.add_resource(
        MerchantUpdateDeliveryStatusResource, "/merchant/orders/<int:order_id>/delivery"
    )

    # ============================================
    # CUSTOMER PURCHASE ROUTES
    # ============================================
    api.add_resource(CustomerPurchaseResource, "/customer/purchase")
    api.add_resource(CustomerGetOrdersResource, "/customer/orders")
    api.add_resource(CustomerPayOrderResource, "/customer/orders/<int:order_id>/pay")

    api.add_resource(CustomerGetProductsResource, "/customer/products")
    api.add_resource(
        CustomerGetProductDetailsResource, "/customer/products/<int:product_id>"
    )
    api.add_resource(
        CustomerGetProductCategoriesResource, "/customer/products/categories"
    )

    # Add to routes.py imports

    # Add to register_routes function:
    # ============================================
    # CUSTOMER PAYMENTS ROUTES
    # ============================================
    api.add_resource(CustomerGetPaymentsResource, "/customer/payments")
    api.add_resource(CustomerGetPaymentStatsResource, "/customer/payments/stats")
    api.add_resource(
        CustomerDownloadReceiptResource, "/customer/payments/<int:payment_id>/receipt"
    )
    # api.add_resource(CustomerInstalmentsResource, "/customer/instalments")
    # In register_routes function, add:

    # ============================================
    # CUSTOMER PAYMENT ROUTES
    # ============================================
    api.add_resource(CustomerPaidPaymentsResource, "/customer/paid-payments")

    api.add_resource(CustomerGetDocumentsResource, "/customer/documents")
    # api.add_resource(CustomerUploadDocumentsResource, "/customer/documents/upload")f
    # api.add_resource(CustomerGetKYCStatusResource, "/customer/kyc/status")

    # ============================================
    # ADMIN DOCUMENT VERIFICATION ROUTES
    # ============================================
    # api.add_resource(AdminGetPendingKYCResource, "/admin/kyc/pending")

    api.add_resource(MerchantGetDocumentsResource, "/merchant/documents")
    api.add_resource(MerchantUploadDocumentsResource, "/merchant/documents/upload")
    api.add_resource(MerchantGetKYCStatusResource, "/merchant/kyc/status")
    api.add_resource(MerchantGetBankDetailsResource, "/merchant/bank-details")
    api.add_resource(MerchantUpdateBankDetailsResource, "/merchant/bank-details")

    # ============================================
    # ADMIN MERCHANT VERIFICATION ROUTES
    # ============================================
    api.add_resource(AdminGetPendingMerchantKYCResource, "/admin/merchant/kyc/pending")
    api.add_resource(
        AdminVerifyMerchantDocumentResource,
        "/admin/merchant/documents/<int:document_id>/verify",
    )
    api.add_resource(
        AdminVerifyMerchantCompleteResource, "/admin/merchant/<int:merchant_id>/verify"
    )
    # routes.py - Add notification routes

    # In register_routes function, add:

    # ============================================
    # NOTIFICATION ROUTES
    # ============================================
    api.add_resource(GetNotificationsResource, "/notifications")
    api.add_resource(
        MarkNotificationReadResource, "/notifications/<int:notification_id>/read"
    )
    api.add_resource(MarkAllNotificationsReadResource, "/notifications/mark-all-read")
    api.add_resource(DeleteNotificationResource, "/notifications/<int:notification_id>")
    api.add_resource(ClearAllNotificationsResource, "/notifications/clear-all")
    api.add_resource(GetUnreadCountResource, "/notifications/unread-count")

    # api.add_resource(MerchantGetNotificationsResource, "/merchant/notifications")
    api.add_resource(
        MerchantMarkNotificationReadResource,
        "/merchant/notifications/<string:notification_id>/read",
    )
    api.add_resource(
        MerchantMarkAllNotificationsReadResource,
        "/merchant/notifications/mark-all-read",
    )
    # api.add_resource(MerchantGetNotificationSettingsResource, "/merchant/notifications/settings")
    # api.add_resource(MerchantUpdateNotificationSettingsResource, "/merchant/notifications/settings")
    api.add_resource(
        MerchantUnreadNotificationCountResource, "/merchant/notifications/unread-count"
    )
    api.add_resource(
        MerchantDeleteNotificationResource,
        "/merchant/notifications/<string:notification_id>",
    )
    api.add_resource(
        MerchantClearAllNotificationsResource, "/merchant/notifications/clear-all"
    )
    # Add to your API routes
    api.add_resource(MerchantPayoutStatsResource, "/merchant/payouts/stats")
    api.add_resource(MerchantRecentPayoutsResource, "/merchant/payouts/recent")
    # routes.py - Add these imports

    api.add_resource(ForgotPasswordResource, "/forgot-password")
    # routes.py - Change the route path
    api.add_resource(VerifyResetOTPResource, "/api/verify-otp")
    api.add_resource(ResetPasswordResource, "/reset-password")
    # In your main app file or routes file
    api.add_resource(CheckUserExistsResource, "/api/check-user-exists")
    # Add these to your route registration

    # KYC/KYB Admin Routes
    api.add_resource(AdminGetPendingKYCResource, "/admin/kyc/pending")
    api.add_resource(AdminGetVerifiedKYCResource, "/admin/kyc/verified")
    api.add_resource(AdminGetRejectedKYCResource, "/admin/kyc/rejected")
    api.add_resource(
        AdminGetMerchantKYCResource, "/admin/kyc/merchant/<int:merchant_id>"
    )
    api.add_resource(AdminApproveKYCResource, "/admin/kyc/approve/<int:merchant_id>")
    api.add_resource(AdminRejectKYCResource, "/admin/kyc/reject/<int:merchant_id>")
    api.add_resource(
        AdminApproveDocumentResource, "/admin/kyc/document/approve/<int:document_id>"
    )
    api.add_resource(
        AdminRejectDocumentResource, "/admin/kyc/document/reject/<int:document_id>"
    )

    # Add to your routes registration

    # Customer KYC Admin Routes
    api.add_resource(AdminGetPendingCustomerKYCResource, "/admin/kyc/customers/pending")
    api.add_resource(
        AdminGetVerifiedCustomerKYCResource, "/admin/kyc/customers/verified"
    )
    api.add_resource(
        AdminGetRejectedCustomerKYCResource, "/admin/kyc/customers/rejected"
    )
    api.add_resource(
        AdminApproveCustomerKYCResource, "/admin/kyc/customer/approve/<int:customer_id>"
    )
    api.add_resource(
        AdminRejectCustomerKYCResource, "/admin/kyc/customer/reject/<int:customer_id>"
    )
    api.add_resource(
        AdminApproveCustomerDocumentResource,
        "/admin/kyc/customer/document/approve/<int:document_id>",
    )
    api.add_resource(
        AdminRejectCustomerDocumentResource,
        "/admin/kyc/customer/document/reject/<int:document_id>",
    )

    api.add_resource(AdminDashboardStatsResource, "/admin/dashboard/stats")
    api.add_resource(AdminRecentTransactionsResource, "/admin/dashboard/transactions")
    api.add_resource(AdminCustomerStatsResource, "/admin/customers/stats")
    api.add_resource(AdminGetCustomersResource, "/admin/customers")
    api.add_resource(
        AdminGetCustomerDetailResource, "/admin/customers/<int:customer_id>"
    )
    api.add_resource(
        AdminUpdateCustomerStatusResource, "/admin/customers/<int:customer_id>/status"
    )
    api.add_resource(
        AdminCreditLimitOverrideResource,
        "/admin/customers/<int:customer_id>/credit-limit",
    )
    api.add_resource(
        AdminAddCustomerNoteResource, "/admin/customers/<int:customer_id>/note"
    )

    # Merchant Statistics
    api.add_resource(AdminMerchantStatsResource, "/admin/merchants/stats")

    # Get all merchants (with filters, search, pagination)
    api.add_resource(AdminGetMerchantsResource, "/admin/merchants")

    # Get single merchant details
    api.add_resource(
        AdminGetMerchantDetailResource, "/admin/merchants/<int:merchant_id>"
    )

    # Update merchant status
    api.add_resource(
        AdminUpdateMerchantStatusResource, "/admin/merchants/<int:merchant_id>/status"
    )

    # Update merchant commission rate
    api.add_resource(
        AdminUpdateMerchantCommissionResource,
        "/admin/merchants/<int:merchant_id>/commission",
    )

    # Adjust merchant reserve amount
    api.add_resource(
        AdminAdjustMerchantReserveResource, "/admin/merchants/<int:merchant_id>/reserve"
    )
    # Customer Management Routes
    # ... rest of your routes

    # Transaction Management Routes
    api.add_resource(AdminTransactionStatsResource, "/admin/transactions/stats")
    api.add_resource(AdminGetTransactionsResource, "/admin/transactions")
    api.add_resource(
        AdminGetTransactionDetailResource, "/admin/transactions/<int:transaction_id>"
    )
    api.add_resource(
        AdminUpdateTransactionStatusResource,
        "/admin/transactions/<int:transaction_id>/status",
    )
    api.add_resource(
        AdminUpdateDeliveryStatusResource,
        "/admin/transactions/<int:transaction_id>/delivery",
    )
    api.add_resource(
        AdminRefundTransactionResource,
        "/admin/transactions/<int:transaction_id>/refund",
    )
    api.add_resource(AdminExportTransactionsResource, "/admin/transactions/export")

    # Instalment Management Routes
    api.add_resource(AdminInstalmentStatsResource, "/admin/instalments/stats")
    api.add_resource(AdminGetInstalmentPlansResource, "/admin/instalments")
    api.add_resource(
        AdminGetInstalmentPlanDetailResource, "/admin/instalments/<int:plan_id>"
    )
    api.add_resource(
        AdminUpdateInstalmentStatusResource, "/admin/instalments/<int:plan_id>/status"
    )
    api.add_resource(
        AdminApplyLateFeeResource,
        "/admin/instalments/payments/<int:payment_id>/apply-late-fee",
    )
    api.add_resource(
        AdminWaiveLateFeeResource,
        "/admin/instalments/payments/<int:payment_id>/waive-late-fee",
    )
    api.add_resource(
        AdminMarkPaymentAsPaidResource,
        "/admin/instalments/payments/<int:payment_id>/mark-paid",
    )
    api.add_resource(AdminExportInstalmentsResource, "/admin/instalments/export")

    # Collection Management Routes
    api.add_resource(AdminCollectionStatsResource, "/admin/collection/stats")
    api.add_resource(AdminGetOverduePaymentsResource, "/admin/collection/overdue")
    api.add_resource(
        AdminGetOverduePaymentDetailResource,
        "/admin/collection/overdue/<int:payment_id>",
    )
    api.add_resource(
        AdminSendPaymentReminderResource, "/admin/collection/<int:payment_id>/reminder"
    )
    api.add_resource(
        AdminMarkPaymentReceivedResource,
        "/admin/collection/<int:payment_id>/mark-received",
    )
    api.add_resource(
        AdminSetPaymentPlanResource, "/admin/collection/<int:payment_id>/payment-plan"
    )
    api.add_resource(AdminExportOverduePaymentsResource, "/admin/collection/export")

    # Settlement Management Routes
    api.add_resource(AdminSettlementStatsResource, "/admin/settlements/stats")
    api.add_resource(AdminGetSettlementsResource, "/admin/settlements")
    api.add_resource(
        AdminGetSettlementDetailResource, "/admin/settlements/<int:settlement_id>"
    )
    api.add_resource(AdminProcessSettlementResource, "/admin/settlements/process-bulk")
    api.add_resource(
        AdminProcessSingleSettlementResource,
        "/admin/settlements/<int:settlement_id>/process",
    )
    api.add_resource(AdminExportSettlementsResource, "/admin/settlements/export")
    api.add_resource(AdminReportRevenueResource, "/admin/reports/revenue")
    api.add_resource(AdminReportTransactionsResource, "/admin/reports/transactions")
    api.add_resource(AdminReportCustomersResource, "/admin/reports/customers")
    api.add_resource(AdminReportMerchantsResource, "/admin/reports/merchants")
    api.add_resource(AdminReportInstalmentsResource, "/admin/reports/instalments")
    api.add_resource(AdminReportDownloadResource, "/admin/reports/download")
    api.add_resource(AdminDashboardKPIResource, "/admin/reports/kpis")
    api.add_resource(AdminProductStatsResource, "/admin/products/stats")
    api.add_resource(AdminGetProductsResource, "/admin/products")
    api.add_resource(AdminGetProductDetailResource, "/admin/products/<int:product_id>")
    api.add_resource(
        AdminUpdateProductStatusResource, "/admin/products/<int:product_id>/status"
    )
    api.add_resource(
        AdminUpdateProductFeaturedResource, "/admin/products/<int:product_id>/featured"
    )
    api.add_resource(
        AdminUpdateProductStockResource, "/admin/products/<int:product_id>/stock"
    )
    api.add_resource(AdminExportProductsResource, "/admin/products/export")

    # User Management Routes
    api.add_resource(AdminUserStatsResource, "/admin/users/stats")
    api.add_resource(AdminGetAllUsersResource, "/admin/users")
    api.add_resource(AdminGetUserDetailResource, "/admin/users/<int:user_id>")
    api.add_resource(AdminUpdateUserStatusResource, "/admin/users/<int:user_id>/status")
    api.add_resource(AdminDeleteUserResource, "/admin/users/<int:user_id>")
    api.add_resource(AdminExportUsersResource, "/admin/users/export")
    # api.add_resource(SystemSettingsResource, '/api/system-settings')
    # api.add_resource(InstallmentOptionsResource, '/api/system-settings/installment-options')

    init_docs(app)
