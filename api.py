import cdsapi

client = cdsapi.Client(
    url="https://cds.climate.copernicus.eu/api",
    key="323a3042-fb70-4fab-8a16-0d945049d9bd"
)

print("Starting download 3: ERA5 Monthly Observations (Ground Truth)...")
client.retrieve(
    "reanalysis-era5-single-levels-monthly-means",  # <--- تغییر حیاتی: دیتای ماهانه
    {
        "product_type": "monthly_averaged_reanalysis", # <--- تغییر حیاتی
        "variable": [
            "2m_temperature",
            "total_precipitation"
        ],
        "year": [
            "2000", "2001", "2002", "2003", "2004", "2005",
            "2006", "2007", "2008", "2009", "2010", "2011",
            "2012", "2013", "2014", "2015", "2016", "2017",
            "2018", "2019", "2020", "2021", "2022", "2023", "2024"
        ],
        "month": [
            "01", "02", "03", "04", "05", "06",
            "07", "08", "09", "10", "11", "12"
        ],
        "time": "00:00", # در دیتای ماهانه فقط همین یک زمان کافیست
        "data_format": "netcdf",
        "download_format": "unarchived",
        "area": [45, -80, 40, -71]        
    },
    "era5_monthly_observations_ny.nc"            
)

print("Download completed successfully!")