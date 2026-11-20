-- Fixture schema: UTF-8 without BOM, GO batch separators. Creates the database named in directory.json.
CREATE DATABASE [KanzleiDB];
GO
USE [KanzleiDB];
GO
CREATE TABLE dbo.Mandant (
    MandantID int IDENTITY(1,1) PRIMARY KEY,
    Name      nvarchar(200) NOT NULL,
    Ort       nvarchar(100) NULL
);
CREATE TABLE dbo.Mandat (
    MandatNr  nvarchar(20)  PRIMARY KEY,
    MandantID int           NOT NULL REFERENCES dbo.Mandant(MandantID),
    Titel     nvarchar(300) NOT NULL,
    Eroeffnet date          NOT NULL,
    Partner   nvarchar(20)  NOT NULL
);
GO
