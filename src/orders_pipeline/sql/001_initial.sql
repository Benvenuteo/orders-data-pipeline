-- The administrator applies migrations. The pipeline account cannot change schemas.
IF SCHEMA_ID(N'control') IS NULL EXEC(N'CREATE SCHEMA control');
IF SCHEMA_ID(N'raw') IS NULL EXEC(N'CREATE SCHEMA raw');
IF SCHEMA_ID(N'staging') IS NULL EXEC(N'CREATE SCHEMA staging');
IF SCHEMA_ID(N'core') IS NULL EXEC(N'CREATE SCHEMA core');
IF SCHEMA_ID(N'quality') IS NULL EXEC(N'CREATE SCHEMA quality');
IF SCHEMA_ID(N'reporting') IS NULL EXEC(N'CREATE SCHEMA reporting');
GO
CREATE TABLE control.schema_versions (
    version int NOT NULL PRIMARY KEY,
    applied_at datetime2(6) NOT NULL DEFAULT SYSUTCDATETIME()
);
CREATE TABLE control.pipeline_state (
    singleton tinyint NOT NULL PRIMARY KEY CHECK (singleton=1),
    source_id varchar(36) NULL,
    last_change_id bigint NOT NULL CHECK (last_change_id>=0)
);
INSERT INTO control.pipeline_state VALUES(1,NULL,0);
CREATE TABLE control.pipeline_runs (
    run_id uniqueidentifier NOT NULL PRIMARY KEY,
    parent_run_id uniqueidentifier NULL,
    kind varchar(12) NOT NULL CHECK(kind IN ('import','replay')),
    status varchar(12) NOT NULL CHECK(status IN ('running','succeeded','failed')),
    started_at datetime2(6) NOT NULL DEFAULT SYSUTCDATETIME(),
    finished_at datetime2(6) NULL,
    source_id varchar(36) NULL,
    lower_id bigint NOT NULL,
    upper_id bigint NULL,
    extraction_complete bit NOT NULL DEFAULT 0,
    fetched int NOT NULL DEFAULT 0,
    inserted int NOT NULL DEFAULT 0,
    updated int NOT NULL DEFAULT 0,
    tombstones int NOT NULL DEFAULT 0,
    rejected int NOT NULL DEFAULT 0,
    error_code varchar(100) NULL,
    CONSTRAINT fk_runs_parent FOREIGN KEY(parent_run_id) REFERENCES control.pipeline_runs(run_id)
);
CREATE INDEX ix_runs_started ON control.pipeline_runs(started_at DESC);
CREATE TABLE raw.api_responses (
    run_id uniqueidentifier NOT NULL REFERENCES control.pipeline_runs(run_id),
    page_no int NOT NULL,
    after_id bigint NOT NULL,
    payload nvarchar(max) NOT NULL,
    fetched_at datetime2(6) NOT NULL DEFAULT SYSUTCDATETIME(),
    PRIMARY KEY(run_id,page_no)
);
CREATE TABLE staging.orders (
    run_id uniqueidentifier NOT NULL REFERENCES control.pipeline_runs(run_id),
    order_id bigint NOT NULL,
    customer_id bigint NOT NULL,
    total_gross decimal(14,2) NOT NULL CHECK(total_gross>=0),
    currency char(3) NOT NULL CHECK(currency IN ('PLN','EUR','USD')),
    status varchar(12) NOT NULL CHECK(status IN ('new','paid','shipped','cancelled')),
    updated_at datetime2(6) NOT NULL,
    is_deleted bit NOT NULL,
    change_id bigint NOT NULL,
    PRIMARY KEY(run_id,order_id)
);
CREATE TABLE core.orders (
    order_id bigint NOT NULL PRIMARY KEY,
    customer_id bigint NOT NULL,
    total_gross decimal(14,2) NOT NULL CHECK(total_gross>=0),
    currency char(3) NOT NULL CHECK(currency IN ('PLN','EUR','USD')),
    status varchar(12) NOT NULL CHECK(status IN ('new','paid','shipped','cancelled')),
    updated_at datetime2(6) NOT NULL,
    is_deleted bit NOT NULL,
    last_change_id bigint NOT NULL,
    last_run_id uniqueidentifier NOT NULL REFERENCES control.pipeline_runs(run_id)
);
CREATE TABLE quality.rejected_records (
    run_id uniqueidentifier NOT NULL REFERENCES control.pipeline_runs(run_id),
    record_no int NOT NULL,
    change_id bigint NULL,
    payload nvarchar(max) NOT NULL,
    reasons nvarchar(max) NOT NULL,
    PRIMARY KEY(run_id,record_no)
);
INSERT INTO control.schema_versions(version) VALUES(1);
GO
CREATE VIEW reporting.active_orders AS
SELECT order_id,customer_id,total_gross,currency,status,updated_at
FROM core.orders WHERE is_deleted=0;
GO
CREATE VIEW reporting.sales_by_currency AS
SELECT currency,COUNT_BIG(*) AS order_count,SUM(total_gross) AS total_gross
FROM core.orders WHERE is_deleted=0 AND status IN ('paid','shipped')
GROUP BY currency;
GO
CREATE VIEW reporting.pipeline_health AS
SELECT TOP (1) run_id,status,started_at,finished_at,fetched,rejected,error_code
FROM control.pipeline_runs ORDER BY started_at DESC;
