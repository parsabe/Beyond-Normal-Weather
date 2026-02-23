# ==========================================
# 3. Downloading real past dataset(ERA5 Observations)
# ==========================================
import cdsapi

client = cdsapi.Client()

print("Starting download 3: ERA5 Observations (Ground Truth)...")
client.retrieve(
    "reanalysis-era5-single-levels",
    {
        "product_type": ["reanalysis"],
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
        "day": [
            "01", "02", "03", "04", "05", "06", "07", "08", "09", 
            "10", "11", "12", "13", "14", "15", "16", "17", "18", 
            "19", "20", "21", "22", "23", "24", "25", "26", "27", 
            "28", "29", "30", "31"
        ],
        "time": [
            "06:00", "07:00", "08:00", "09:00", "10:00", "12:00",
            "13:00", "14:00", "15:00", "16:00", "17:00", "18:00"
        ],
        "data_format": "netcdf",
        "download_format": "unarchived",
        "area": [45, -80, 40, -71]        
    },
    "era5_observations_ny.nc"            
)

print("All datasets (Historical, Future, and ERA5) successfully downloaded!")