# v1.0.0

Customer deploy bundle for Knovas.

## KnovasPlatform

Docker search UI for an indexed Knovas tenant. Requires mTLS client certificates and company login configuration.

- Deploy: [KnovasPlatform/docs/first-deploy-checklist.md](KnovasPlatform/docs/first-deploy-checklist.md)
- API reference: [KnovasPlatform/knovas-docs/Knovas_Developer_Implementation_Kit/README.md](KnovasPlatform/knovas-docs/Knovas_Developer_Implementation_Kit/README.md)

## RemoteController

Discover and sync local text files to Knovas (employee JWT + RC mTLS at the edge; tenant mTLS for ingestion).

- Deploy: [RemoteController/docs/hosting/GETTING_STARTED.md](RemoteController/docs/hosting/GETTING_STARTED.md)

## Prerequisites (from Knovas)

- Tenant mTLS certificates
- Documents indexed in Knovas (via RemoteController or your ingestion pipeline)
- For RemoteController: instance token, employee RC certificates, registered public URL
