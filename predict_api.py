import cdsapi

client = cdsapi.Client(
    url="https://cds.climate.copernicus.eu/api",
    key="323a3042-fb70-4fab-8a16-0d945049d9bd"
)
print("Starting download 1: Historical Temperature (GFDL-ESM4)...")
client.retrieve(
    "projections-cmip6",
    {
        "temporal_resolution": "monthly",
        "experiment": "historical",
        "variable": "near_surface_air_temperature",
        "model": "gfdl_esm4",
        "month": [
            "01", "02", "03", "04", "05", "06",
            "07", "08", "09", "10", "11", "12"
        ],
        "year": [
            "1990", "1991", "1992", "1993", "1994", 
            "1995", "1996", "1997", "1998", "1999", 
            "2000", "2001", "2002", "2003", "2004", 
            "2005", "2006", "2007", "2008", "2009", 
            "2010", "2011", "2012", "2013", "2014"
        ],
        "area": [45, -80, 40, -71] # باگ -40 برطرف شد
    },
    "historical_temp_ny.zip" # ذخیره با اسم مشخص
)

# ==========================================
# 2. دانلود دیتای آینده (Future Precipitation)
# ==========================================
print("Starting download 2: Future Precipitation (MPI-ESM1-2-LR)...")
client.retrieve(
    "projections-cmip6",
    {
        "temporal_resolution": "monthly",
        "experiment": "ssp2_4_5",
        "variable": "precipitation",
        "model": "mpi_esm1_2_lr",
        "month": [
            "01", "02", "03", "04", "05", "06",
            "07", "08", "09", "10", "11", "12"
        ],
        "year": [
            "2015", "2016", "2017", "2018", "2019", "2020",
            "2021", "2022", "2023", "2024", "2025", "2026",
            "2027", "2028", "2029", "2030", "2031", "2032",
            "2033", "2034", "2035", "2036", "2037", "2038",
            "2039", "2040", "2041", "2042", "2043", "2044",
            "2045", "2046", "2047", "2048", "2049"
        ],
        "area": [45, -80, 40, -71] # باگ -40 برطرف شد
    },
    "ssp245_precip_ny.zip" # ذخیره با اسم مشخص
)

print("All downloads completed successfully!")