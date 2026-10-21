# NovaWorks PeopleOps AI HR Copilot

An AI-powered HR copilot demonstrating policy question answering, read-only HR data queries, and permission-controlled HR actions.

## Assignment

**Assignment 05 — AI HR Copilot**

This repository contains the application, AI components, tests, evaluation evidence, architecture documentation, and security review for the assignment.

## Key capabilities

- **Policy RAG:** Retrieve HR policy information and provide grounded answers with source references and abstention when evidence is insufficient.
- **SQL Agent:** Support read-only HR data queries with SQL validation, access scoping, and sensitive-field restrictions.
- **HR Action Agent:** Propose supported HR actions through authorized backend APIs with permission checks and confirmation controls.
- **Role-based access:** Apply employee, manager, and admin permissions on the backend.
- **AI audit logging:** Record relevant AI activity while limiting stored message content.
- **Evaluation and testing:** Include automated tests, offline evaluation suites, and documented live-model evaluation evidence.

## Repository structure

- `HRMS-main/` — Application source code and project documentation.
- `HRMS-main/backend/` — Backend application and API.
- `HRMS-main/frontend/` — Frontend application.
- `HRMS-main/docs/` — Assignment documentation, architecture, permissions, evaluation results, and security reviews.
- `.github/` — CI workflows and security checks.
- `CLAUDE.md` — Repository-specific development guidance.

## Start here

1. Read [`HRMS-main/README.md`](HRMS-main/README.md) for project setup, configuration, API endpoints, and test instructions.
2. Review [`docs/ai_architecture.md`](HRMS-main/docs/ai_architecture.md) for the AI architecture.
3. Review [`docs/ai_permissions_matrix.md`](HRMS-main/docs/ai_permissions_matrix.md) for role permissions.
4. Review [`docs/ai_eval_results.md`](HRMS-main/docs/ai_eval_results.md) for evaluation evidence and limitations.
5. Read [`docs/assignment-05-feature-audit.md`](HRMS-main/docs/assignment-05-feature-audit.md) and [`docs/capstone-submission-report.md`](HRMS-main/docs/capstone-submission-report.md) for assignment coverage and submission evidence.
6. Review [`docs/final-security-readiness.md`](HRMS-main/docs/final-security-readiness.md) for security findings.

## Testing and evaluation

Refer to the project README and evaluation report for commands, results, and test limitations.

Automated test results, live-model evaluations, CI results, and manual browser verification are separate forms of evidence and should be interpreted separately.

## Known limitations

- The latest reported GitHub Actions run passed the backend, frontend, browser E2E, production image build, and secret scan jobs; the dependency advisory job failed.
- Manual browser walkthrough scenarios remain pending unless separately completed and documented.
- The project is **not yet production-ready**.

See the [security review](HRMS-main/docs/final-security-readiness.md) and [release-readiness](HRMS-main/docs/production-hardening/release-readiness.md) documents for details.

## Security and configuration

- Do not commit `.env` files, API keys, credentials, or real sensitive HR data.
- Use synthetic data for demonstrations and testing.
- Review the security and permissions documentation before deployment.

## License

No license file is included in this repository. The application README ends with the notice "Copyright (c) Codebasics.
All rights reserved."
