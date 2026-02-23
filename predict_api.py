import cdsapi

dataset = "projections-cmip6"
request = {
    "temporal_resolution": "monthly",
    "experiment": "historical",
    "variable": "near_surface_air_temperature",
    "model": "gfdl_esm4",
    "month": [
        "01", "02", "03",
        "04", "05", "06",
        "07", "08", "09",
        "10", "11", "12"
    ],
    "year": [
        "2000", "2001", "2002",
        "2003", "2004", "2005",
        "2006", "2007", "2008",
        "2009", "2010", "2011",
        "2012", "2013", "2014",
        "1990", "1995", "1991",
        "1996", "1992", "1997",
        "1993", "1998", "1994",
        "1999"
    ],
    "area": [45, -80, -40, -71]
}

client = cdsapi.Client()
client.retrieve(dataset, request).download()
