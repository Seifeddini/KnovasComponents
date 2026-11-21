-- Fixture rows (synthetic). N'' literals keep umlauts in nvarchar columns.
SET NOCOUNT ON;
INSERT INTO dbo.Mandant (Name, Ort) VALUES
    (N'Bäckerei Zürcher AG', N'Zürich'),
    (N'Müller Söhne GmbH', N'Winterthur'),
    (N'Stiftung Grünau', N'Uster');
GO
INSERT INTO dbo.Mandat (MandatNr, MandantID, Titel, Eroeffnet, Partner) VALUES
    (N'2019-0042', 1, N'Forderungsklage gegen Lieferantin', '2019-05-02', N'mbaer'),
    (N'2021-0107', 2, N'Prüfung Mietvertrag Gewerberäume', '2021-02-15', N'aroth'),
    (N'2022-0013', 3, N'Statutenänderung', '2022-01-10', N'mbaer');
GO
