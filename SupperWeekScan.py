#!/usr/bin/env python3
"""Weekly stock scanner for selected Indian stocks.

This scanner reads weekly OHLC candles through the Groww API, applies a weekly
Supertrend-style proxy, and emits a scheduler-readable trade signal when the
weekly trend flips. The main scheduler runs the script every Friday at 11:00 PM IST.
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from growwapi import GrowwAPI


def load_groww_credentials():
    """Load Groww creds from environment or a local .env file."""
    env_file = Path(__file__).resolve().with_name(".env")
    if env_file.exists():
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                os.environ.setdefault(key, value)
        except Exception:
            pass

    api_key = os.getenv("GROWW_API_KEY", "").strip()
    api_secret = os.getenv("GROWW_API_SECRET", "").strip()
    return api_key, api_secret


WATCHLIST = [
    '20MICRONS', '21STCENMGM', '360ONE', '3BBLACKBIO', '3IINFOLTD', '3MINDIA',
    '3PLAND', '5PAISA', '63MOONS', 'A2ZINFRA', 'AAATECH', 'AADHARHFC',
    'AAKASH', 'AAREYDRUGS', 'AARNAV', 'AARON', 'AARTECH', 'AARTIDRUGS',
    'AARTIIND', 'AARTIPHARM', 'AARTISURF', 'AARVI', 'AASTHA', 'AAVAS',
    'ABAN', 'ABANSENT', 'ABB', 'ABBOTINDIA', 'ABCAPITAL', 'ABCOTS',
    'ABDL', 'ABFRL', 'ABINFRA', 'ABLBL', 'ABMINTLLTD', 'ABMKNO',
    'ABREL', 'ABSLAMC', 'ACC', 'ACCELYA', 'ACCURACY', 'ACE',
    'ACEINTEG', 'ACI', 'ACL', 'ACMESOLAR', 'ACSTECH', 'ACUTAAS',
    'ADANIENSOL', 'ADANIENT', 'ADANIGREEN', 'ADANIPORTS', 'ADANIPOWER', 'ADFFOODS',
    'ADL', 'ADOR', 'ADROITINFO', 'ADSL', 'ADVAIT', 'ADVANCE',
    'ADVANIHOTR', 'ADVENTHTL', 'ADVENZYMES', 'AEGISLOG', 'AEGISVOPAK', 'AEPL',
    'AEQUS', 'AEROENTER', 'AEROFLEX', 'AERONEU', 'AEROPLANE', 'AETHER',
    'AFCONS', 'AFFLE', 'AFFORDABLE', 'AFIL', 'AFSL', 'AGARIND',
    'AGARWALEYE', 'AGI', 'AGIIL', 'AGL', 'AGRITECH', 'AGROPHOS',
    'AGSTRA', 'AHCL', 'AHLADA', 'AHLEAST', 'AHLUCONT', 'AHLWEST',
    'AIAENG', 'AIIL', 'AIRAN', 'AIROLAM', 'AJANTPHARM', 'AJAXENGG',
    'AJMERA', 'AJOONI', 'AKASH', 'AKCAPIT', 'AKG', 'AKI',
    'AKSHAR', 'AKSHARCHEM', 'AKSHOPTFBR', 'AKUMS', 'ALANKIT', 'ALBERTDAVD',
    'ALEMBICLTD', 'ALGOQUANT', 'ALICON', 'ALIVUS', 'ALKALI', 'ALKEM',
    'ALKYLAMINE', 'ALLCARGO', 'ALLDIGI', 'ALLTIME', 'ALMONDZ', 'ALOKINDS',
    'ALPA', 'ALPHAGEO', 'ALPINETEX', 'AMAGI', 'AMANTA', 'AMBALALSA',
    'AMBER', 'AMBICAAGAR', 'AMBIKCO', 'AMBUJACEM', 'AMDIND', 'AMJLAND',
    'AMNPLST', 'AMRUTANJAN', 'ANANDRATHI', 'ANANTRAJ', 'ANDHRAPAP', 'ANDHRSUGAR',
    'ANGELONE', 'ANIKINDS', 'ANKITMETAL', 'ANMOL', 'ANNAPURNA', 'ANSALAPI',
    'ANTELOPUS', 'ANTGRAPHIC', 'ANTHEM', 'ANUHPHR', 'ANUP', 'ANURAS',
    'APARINDS', 'APCL', 'APCOTEXIND', 'APEX', 'APLAPOLLO', 'APLLTD',
    'APOLLO', 'APOLLOHOSP', 'APOLLOPIPE', 'APOLLOTYRE', 'APOLSINHOT', 'APTECHT',
    'APTUS', 'AQYLON', 'ARCHIDPLY', 'ARCHIES', 'ARDEE', 'ARE&M',
    'ARENTERP', 'ARFIN', 'ARIES', 'ARIHANT', 'ARIHANTCAP', 'ARIHANTSUP',
    'ARIS', 'ARKADE', 'ARMANFIN', 'AROGRANITE', 'ARROWGREEN', 'ARSHIYA',
    'ARSSBL', 'ARTEMISMED', 'ARTNIRMAN', 'ARVEE', 'ARVIND', 'ARVINDFASN',
    'ARVSMART', 'ASAHIINDIA', 'ASAHISONG', 'ASAL', 'ASALCBR', 'ASHAPURMIN',
    'ASHIANA', 'ASHIKAG', 'ASHIMASYN', 'ASHOKA', 'ASHOKAMET', 'ASHOKLEY',
    'ASIANENE', 'ASIANHOTNR', 'ASIANPAINT', 'ASIANTILES', 'ASKAUTOLTD', 'ASMS',
    'ASPINWALL', 'ASTAR', 'ASTEC', 'ASTERDM', 'ASTRAL', 'ASTRAMICRO',
    'ASTRAZEN', 'ASTRON', 'ATALREAL', 'ATAM', 'ATGL', 'ATHERENERG',
    'ATL', 'ATLANTAA', 'ATLANTAELE', 'ATLASCYCLE', 'ATUL', 'ATULAUTO',
    'AUBANK', 'AURIONPRO', 'AUROPHARMA', 'AURUM', 'AURUS', 'AUSOMENT',
    'AUTOAXLES', 'AUTOIND', 'AVADHSUGAR', 'AVALON', 'AVANTEL', 'AVANTIFEED',
    'AVG', 'AVL', 'AVONMORE', 'AVROIND', 'AVTNPL', 'AWFIS',
    'AWHCL', 'AWL', 'AXISBANK', 'AXISCADES', 'AXITA', 'AYE',
    'AYMSYNTEX', 'AZAD', 'BAFNAPH', 'BAGFILMS', 'BAIDFIN', 'BAJAJ-AUTO',
    'BAJAJCON', 'BAJAJELEC', 'BAJAJFINSV', 'BAJAJHCARE', 'BAJAJHFL', 'BAJAJHIND',
    'BAJAJHLDNG', 'BAJAJINDEF', 'BAJAJST', 'BAJEL', 'BAJFINANCE', 'BALAJEE',
    'BALAJITELE', 'BALAMINES', 'BALAXI', 'BALKRISHNA', 'BALKRISIND', 'BALMLAWRIE',
    'BALPHARMA', 'BALRAMCHIN', 'BALUFORGE', 'BANARBEADS', 'BANARISUG', 'BANCOINDIA',
    'BANDHANBNK', 'BANG', 'BANKA', 'BANKBARODA', 'BANKINDIA', 'BANSALWIRE',
    'BANSWRAS', 'BASF', 'BASML', 'BATAINDIA', 'BATLIBOI', 'BAYERCROP',
    'BBL', 'BBOX', 'BBTC', 'BBTCL', 'BCG', 'BCLIND',
    'BCONCEPTS', 'BCPL', 'BDL', 'BEARDSELL', 'BECTORFOOD', 'BEDMUTHA',
    'BEEKAY', 'BEL', 'BELLACASA', 'BELRISE', 'BEML', 'BENGALASM',
    'BEPL', 'BERGEPAINT', 'BESTAGRO', 'BETA', 'BFINVEST', 'BFUTILITIE',
    'BGRENERGY', 'BHAGCHEM', 'BHAGERIA', 'BHAGYANGR', 'BHANDARI', 'BHARATCOAL',
    'BHARATFORG', 'BHARATGEAR', 'BHARATRAS', 'BHARATSE', 'BHARATWIRE', 'BHARTIARTL',
    'BHARTIHEXA', 'BHEL', 'BI', 'BIGBLOC', 'BIKAJI', 'BIL',
    'BILVYAPAR', 'BIMETAL', 'BIOCON', 'BIOFILCHEM', 'BIRLACABLE', 'BIRLACORPN',
    'BIRLAMONEY', 'BIRLANU', 'BIRLAPREC', 'BLACKBUCK', 'BLACKROSE', 'BLAL',
    'BLBLIMITED', 'BLIL', 'BLISSGVS', 'BLKASHYAP', 'BLS', 'BLSE',
    'BLUECHIP', 'BLUECOAST', 'BLUEDART', 'BLUEJET', 'BLUESTARCO', 'BLUESTONE',
    'BLUSPRING', 'BMWVENTLTD', 'BNAGROCHEM', 'BNALTD', 'BODALCHEM', 'BOHRAIND',
    'BOMDYEING', 'BONLON', 'BORANA', 'BOROLTD', 'BORORENEW', 'BOROSCI',
    'BOSCH-HCIL', 'BOSCHLTD', 'BPCL', 'BPL', 'BRIGADE', 'BRIGHOTEL',
    'BRITANNIA', 'BRNL', 'BROOKS', 'BSE', 'BSHSL', 'BSL',
    'BSOFT', 'BTML', 'BTTL', 'BUILDPRO', 'BURNPUR', 'BUTTERFLY',
    'BVCL', 'BYKE', 'CALSOFT', 'CAMLINFINE', 'CAMPUS', 'CAMS',
    'CANBK', 'CANFINHOME', 'CANHLIFE', 'CANTABIL', 'CAPACITE', 'CAPILLARY',
    'CAPITALSFB', 'CAPLIPOINT', 'CAPTRUST', 'CARBORUNIV', 'CARERATING', 'CARRARO',
    'CARTRADE', 'CARYSIL', 'CASTROLIND', 'CCAVENUE', 'CCCL', 'CCHHL',
    'CCL', 'CDSL', 'CEATLTD', 'CEIGALL', 'CEINSYS', 'CELEBRITY',
    'CELLO', 'CEMPRO', 'CENTENKA', 'CENTEXT', 'CENTRALBK', 'CENTRUM',
    'CENTUM', 'CENTURYPLY', 'CERA', 'CEREBRAINT', 'CESC', 'CEWATER',
    'CGCL', 'CGPOWER', 'CHALET', 'CHAMBLFERT', 'CHEMBOND', 'CHEMBONDCH',
    'CHEMCON', 'CHEMFAB', 'CHEMPLASTS', 'CHENNPETRO', 'CHEVIOT', 'CHOICEIN',
    'CHOLAFIN', 'CHOLAHLDNG', 'CIEINDIA', 'CIFL', 'CINELINE', 'CINEVISTA',
    'CIPLA', 'CLCIND', 'CLEAN', 'CLEANMAX', 'CLEDUCATE', 'CLSEL',
    'CMLL', 'CMPDI', 'CMRGREEN', 'CMSINFO', 'CNL', 'COALINDIA',
    'COASTCORP', 'COCHINSHIP', 'COCKERILL', 'COFFEEDAY', 'COFORGE', 'COHANCE',
    'COLPAL', 'COMFINTE', 'COMPINFO', 'COMPUSOFT', 'COMSYN', 'CONCOR',
    'CONCORDBIO', 'CONFIPET', 'CONSOFINVT', 'CONTROLPR', 'CORALFINAC', 'CORDELIA',
    'CORDSCABLE', 'COROMANDEL', 'CORONA', 'COSMOFIRST', 'COUNCODOS', 'CPCAP',
    'CPEDU', 'CPL', 'CPPLUS', 'CRAFTSMAN', 'CRAMC', 'CREATIVEYE',
    'CREDITACC', 'CREST', 'CRISIL', 'CRIZAC', 'CROMPTON', 'CROWN',
    'CSBBANK', 'CSLFINANCE', 'CSM', 'CTE', 'CUB', 'CUBEXTUB',
    'CUMMINSIND', 'CUPID', 'CURAA', 'CYBERMEDIA', 'CYBERTECH', 'CYIENT',
    'CYIENTDLM', 'DABUR', 'DAICHI', 'DALBHARAT', 'DALMIASUG', 'DAMCAPITAL',
    'DAMODARIND', 'DANGEE', 'DATAMATICS', 'DATAPATTNS', 'DAVANGERE', 'DBCORP',
    'DBEIL', 'DBL', 'DBOL', 'DBREALTY', 'DBSTOCKBRO', 'DCAL',
    'DCBBANK', 'DCI', 'DCM', 'DCMFINSERV', 'DCMNVL', 'DCMSHRIRAM',
    'DCMSIL', 'DCMSRIND', 'DCW', 'DCXINDIA', 'DDEVPLSTIK', 'DECCANCE',
    'DECNGOLD', 'DEEDEV', 'DEEPAKFERT', 'DEEPAKNTR', 'DEEPINDS', 'DELHIVERY',
    'DELPHIFX', 'DELTACORP', 'DELTAMAGNT', 'DEN', 'DENORA', 'DENTA',
    'DEVIT', 'DEVX', 'DEVYANI', 'DGCONTENT', 'DHAMPURSUG', 'DHANBANK',
    'DHANUKA', 'DHARAN', 'DHARMAJ', 'DHRUV', 'DHUNINV', 'DIACABS',
    'DIAMINESQ', 'DIAMONDYD', 'DICIND', 'DIFFNKG', 'DIGIDRIVE', 'DIGISPICE',
    'DIGITIDE', 'DIGJAMLMTD', 'DISAQ', 'DISHTV', 'DIVGIITTS', 'DIVISLAB',
    'DIXON', 'DJML', 'DLF', 'DLINKINDIA', 'DMART', 'DMCC',
    'DNAMEDIA', 'DODLA', 'DOLATALGO', 'DOLLAR', 'DOLPHIN', 'DOMS',
    'DONEAR', 'DPABHUSHAN', 'DPSCLTD', 'DPWIRES', 'DRAGARWQ', 'DRCSYSTEMS',
    'DREAMFOLKS', 'DREDGECORP', 'DRREDDY', 'DSFCL', 'DSKULKARNI', 'DSSL',
    'DTIL', 'DUCON', 'DVL', 'DWARKESH', 'DYCL', 'DYNAMATECH',
    'DYNPRO', 'E2E', 'EASEMYTRIP', 'EASTSILK', 'EBGNG', 'ECLERX',
    'ECOSMOBLTY', 'EDELWEISS', 'EFCIL', 'EICHERMOT', 'EIDPARRY', 'EIEL',
    'EIFFL', 'EIHAHOTELS', 'EIHOTEL', 'EIMCOELECO', 'EKC', 'ELANTAS',
    'ELCIDIN', 'ELDEHSG', 'ELECON', 'ELECTCAST', 'ELECTHERM', 'ELGIEQUIP',
    'ELGIRUBCO', 'ELIN', 'ELITECON', 'ELLEN', 'ELPROINTL', 'EMAMILTD',
    'EMAMIPAP', 'EMAMIREAL', 'EMBDL', 'EMCURE', 'EMIL', 'EMKAY',
    'EMMBI', 'EMMVEE', 'EMPOWER', 'EMSLIMITED', 'EMUDHRA', 'ENDURANCE',
    'ENERGYDEV', 'ENGINERSIN', 'ENIL', 'ENRIN', 'ENTERO', 'EPACK',
    'EPACKPEB', 'EPIGRAL', 'EPL', 'EQUIPPP', 'EQUITASBNK', 'ERIS',
    'ESABINDIA', 'ESAFSFB', 'ESCORTS', 'ESSARSHPNG', 'ESSENTIA', 'ESTER',
    'ETERNAL', 'ETHOSLTD', 'EUREKAFORB', 'EUROBOND', 'EUROPRATIK', 'EUROTEXIND',
    'EVEREADY', 'EVERESTIND', 'EXCELINDUS', 'EXCELSOFT', 'EXICOM', 'EXIDEIND',
    'EXPLEOSOL', 'EXXARO', 'FABTECH', 'FACT', 'FAIRCHEMOR', 'FAZE3Q',
    'FCL', 'FCSSOFT', 'FDC', 'FEDDERSHOL', 'FEDERALBNK', 'FEDFINA',
    'FEL', 'FELDVR', 'FERMENTA', 'FIBERWEB', 'FIEMIND', 'FILATEX',
    'FILATFASH', 'FINCABLES', 'FINEORG', 'FINKURVE', 'FINOPB', 'FINPIPE',
    'FIRSTCRY', 'FISCHER', 'FIVESTAR', 'FLAIR', 'FLEXITUFF', 'FLFL',
    'FLUOROCHEM', 'FMGOETZE', 'FMNL', 'FOCUS', 'FOODSIN', 'FORCEMOT',
    'FORTIS', 'FOSECOIND', 'FRACTAL', 'FRONTSP', 'FSL', 'FUSION',
    'GABRIEL', 'GAEL', 'GAIL', 'GALAPREC', 'GALAXYSURF', 'GALLANTT',
    'GANDHAR', 'GANDHITUBE', 'GANECOS', 'GANESHBE', 'GANESHCP', 'GANESHHOU',
    'GANGAFORGE', 'GANGESSECU', 'GARFIBRES', 'GARUDA', 'GATECH', 'GATECHDVR',
    'GATEWAY', 'GAUDIUMIVF', 'GAYAHWS', 'GAYAPROJ', 'GCSL', 'GEECEE',
    'GEEKAYWIRE', 'GEMAROMA', 'GENCON', 'GENESYS', 'GENESYS-RE', 'GENSOL',
    'GENUSPAPER', 'GENUSPOWER', 'GEOJITFSL', 'GESHIP', 'GFLLIMITED', 'GHCL',
    'GHCLTEXTIL', 'GICHSGFIN', 'GICL', 'GICRE', 'GILLANDERS', 'GILLETTE',
    'GINNIFILA', 'GIPCL', 'GKENERGY', 'GKSL', 'GKWLIMITED', 'GLAND',
    'GLAXO', 'GLENMARK', 'GLFL', 'GLOBAL', 'GLOBALE', 'GLOBALVECT',
    'GLOBE', 'GLOBECIVIL', 'GLOBUSSPR', 'GLOSTERLTD', 'GLOTTIS', 'GMBREW',
    'GMDCLTD', 'GMMPFAUDLR', 'GMRAIRPORT', 'GMRP&UI', 'GNA', 'GNFC',
    'GNRL', 'GOACARBON', 'GOCLCORP', 'GOCOLORS', 'GODAVARIB', 'GODFRYPHLP',
    'GODIGIT', 'GODREJAGRO', 'GODREJCP', 'GODREJIND', 'GODREJPROP', 'GOKEX',
    'GOKUL', 'GOKULAGRO', 'GOLDENTOBC', 'GOLDIAM', 'GOLDTECH', 'GOODLUCK',
    'GOODYEAR', 'GOPAL', 'GOYALALUM', 'GPIL', 'GPPL', 'GPTHEALTH',
    'GPTINFRA', 'GRADIENTE', 'GRANDOAK', 'GRANULES', 'GRAPHITE', 'GRASIM',
    'GRAUWEIL', 'GRAVISSHO', 'GRAVITA', 'GREAVESCOT', 'GREENLAM', 'GREENPANEL',
    'GREENPLY', 'GREENPOWER', 'GRINDWELL', 'GRINFRA', 'GRMOVER', 'GROBTEA',
    'GROWW', 'GRPLTD', 'GRSE', 'GRWRHITECH', 'GSFC', 'GSLSU',
    'GSPCROP', 'GSS', 'GTECJAINX', 'GTL', 'GTLINFRA', 'GTPL',
    'GUFICBIO', 'GUJALKALI', 'GUJAPOLLO', 'GUJENERGY', 'GUJRAFFIA', 'GUJTHEM',
    'GULFOILLUB', 'GULFPETRO', 'GULPOLY', 'GVKPIL', 'GVPIL', 'GVPTECH',
    'GVT&D', 'GYFTR', 'HAL', 'HALDER', 'HALDYNGL', 'HALEOSLABS',
    'HAPPSTMNDS', 'HAPPYFORGE', 'HARDWYN', 'HARIOMPIPE', 'HARRMALAYA', 'HARSHA',
    'HATHWAY', 'HATSUN', 'HAVELLS', 'HAVISHA', 'HAWKINCOOK', 'HBESD',
    'HBLENGINE', 'HBSL', 'HCC', 'HCG', 'HCL-INSYS', 'HCLTECH',
    'HDBFS', 'HDFCAMC', 'HDFCBANK', 'HDFCLIFE', 'HDIL', 'HEADSUP',
    'HEALTHX', 'HECPROJECT', 'HEG', 'HEIDELBERG', 'HEMIPROP', 'HERANBA',
    'HERITGFOOD', 'HEROMOTOCO', 'HESTERBIO', 'HEXAGON', 'HEXATRADEX', 'HEXT',
    'HFCL', 'HGINFRA', 'HGM', 'HGS', 'HIKAL', 'HILINFRA',
    'HILTON', 'HIMATSEIDE', 'HINDALCO', 'HINDCOMPOS', 'HINDCON', 'HINDCOPPER',
    'HINDOILEXP', 'HINDPETRO', 'HINDUNILVR', 'HINDWAREAP', 'HINDZINC', 'HIRECT',
    'HISARMETAL', 'HITECH', 'HITECHCORP', 'HITECHGEAR', 'HLEGLAS', 'HLVLTD',
    'HMAAGRO', 'HMT', 'HMVL', 'HNDFDS', 'HOMEFIRST', 'HONASA',
    'HONAUT', 'HONDAPOWER', 'HPAL', 'HPIL', 'HPL', 'HSCL',
    'HTMEDIA', 'HUBTOWN', 'HUDCO', 'HUHTAMAKI', 'HYBRIDFIN', 'HYUNDAI',
    'IBULLSLTD', 'ICDSLTD', 'ICEMAKE', 'ICICIAMC', 'ICICIBANK', 'ICICIGI',
    'ICICIPRULI', 'ICIL', 'ICRA', 'IDBI', 'IDEA', 'IDEAFORGE',
    'IDFCFIRSTB', 'IEX', 'IFBAGRO', 'IFBIND', 'IFCI', 'IFGLEXPOR',
    'IGARASHI', 'IGCL', 'IGIL', 'IGL', 'IGPL', 'IIFL',
    'IIFLCAPS', 'IITL', 'IKIO', 'IKS', 'IL&FSENGG', 'IL&FSTRANS',
    'IMAGICAA', 'IMFA', 'IMPAL', 'IMPEXFERRO', 'INA', 'INCREDIBLE',
    'INDBANK', 'INDGN', 'INDHOTEL', 'INDIACEM', 'INDIAGLYCO', 'INDIAMART',
    'INDIANB', 'INDIANCARD', 'INDIANHUME', 'INDIASHLTR', 'INDIGO', 'INDIGOPNTS',
    'INDIQUBE', 'INDNIPPON', 'INDOAMIN', 'INDOBORAX', 'INDOCO', 'INDOFARM',
    'INDOMIM', 'INDORAMA', 'INDOSTAR', 'INDOTECH', 'INDOTHAI', 'INDOUS',
    'INDOWIND', 'INDPRUD', 'INDRAMEDCO', 'INDSWFTLAB', 'INDTERRAIN', 'INDUSINDBK',
    'INDUSTOWER', 'INFOBEAN', 'INFOMEDIA', 'INFY', 'INGERRAND', 'INNOVACAP',
    'INNOVANA', 'INNOVISION', 'INOXGREEN', 'INOXINDIA', 'INOXWIND', 'INSECTICID',
    'INSPIRISYS', 'INTELLECT', 'INTENTECH', 'INTERARCH', 'INTLCONV', 'INVENTURE',
    'INVPRECQ', 'IOB', 'IOC', 'IOLCP', 'IONEXCHANG', 'IPCALAB',
    'IPL', 'IRB', 'IRCON', 'IRCTC', 'IREDA', 'IRFC',
    'IRIS', 'IRISDOREME', 'IRMENERGY', 'ISFT', 'ISGEC', 'ISHANCH',
    'ITC', 'ITCHOTELS', 'ITDC', 'ITI', 'IVALUE', 'IVC',
    'IVP', 'IWP', 'IXIGO', 'IZMO', 'J&KBANK', 'JAGRAN',
    'JAGSNPHARM', 'JAIBALAJI', 'JAICORPLTD', 'JAINREC', 'JAIPURKURT', 'JAMNAAUTO',
    'JARO', 'JASH', 'JAYAGROGN', 'JAYBARMARU', 'JAYKAY', 'JAYNECOIND',
    'JAYSREETEA', 'JBMA', 'JETFREIGHT', 'JGCHEM', 'JHS', 'JINDALPHOT',
    'JINDALPOLY', 'JINDALSAW', 'JINDALSTEL', 'JINDRILL', 'JINDWORLD', 'JIOFIN',
    'JISLDVREQS', 'JISLJALEQS', 'JITFINFRA', 'JKCEMENT', 'JKIL', 'JKIPL',
    'JKLAKSHMI', 'JKPAPER', 'JKTYRE', 'JLHL', 'JMA', 'JMFINANCIL',
    'JNKINDIA', 'JNPR', 'JOCIL', 'JPOLYINVST', 'JPPOWER', 'JSFB',
    'JSL', 'JSLL', 'JSWCEMENT', 'JSWDULUX', 'JSWENERGY', 'JSWHL',
    'JSWINFRA', 'JSWSTEEL', 'JTEKTINDIA', 'JTLIND', 'JUBLCPL', 'JUBLFOOD',
    'JUBLINGREA', 'JUBLPHARMA', 'JUNIPER', 'JUSTDIAL', 'JWL', 'JYOTHYLAB',
    'JYOTICNC', 'JYOTISTRUC', 'KABRAEXTRU', 'KAJARIACER', 'KAKATCEM', 'KALAMANDIR',
    'KALPATARU', 'KALYANI', 'KALYANIFRG', 'KALYANKJIL', 'KAMAHOLD', 'KAMATHOTEL',
    'KAMDHENU', 'KAMOPAINTS', 'KANANIIND', 'KANCHI', 'KANORICHEM', 'KANPRPLA',
    'KANSAINER', 'KAPSTON', 'KARMAENG', 'KARURVYSYA', 'KAUSHALYA', 'KAVDEFENCE',
    'KAYA', 'KAYNES', 'KCP', 'KCPSUGIND', 'KDDL', 'KEC',
    'KECL', 'KEEPLEARN', 'KEI', 'KELLTONTEC', 'KENNAMET', 'KERNEX',
    'KESORAMIND', 'KEYFINSERV', 'KFINTECH', 'KHADIM', 'KHAICHEM', 'KHAITANLTD',
    'KHANDSE', 'KICL', 'KILITCH', 'KIMS', 'KINGFA', 'KIOCL',
    'KIRANVYPAR', 'KIRIINDUS', 'KIRLFER', 'KIRLOSBROS', 'KIRLOSENG', 'KIRLOSIND',
    'KIRLPNU', 'KISSHT', 'KITEX', 'KKCL', 'KLBRENG-B', 'KMEW',
    'KMSUGAR', 'KNACK', 'KNAGRI', 'KNRCON', 'KOHINOOR', 'KOKUYOCMLN',
    'KOLTEPATIL', 'KOPRAN', 'KOTAKBANK', 'KOTARISUG', 'KOTHARIPET', 'KOTHARIPRO',
    'KOTIC', 'KOTYARK', 'KOVAI', 'KPEL', 'KPIGREEN', 'KPIL',
    'KPITTECH', 'KPL', 'KPRMILL', 'KRBL', 'KREBSBIO', 'KRIDHANINF',
    'KRISHANA', 'KRISHIVAL', 'KRISHNADEF', 'KRITI', 'KRITIKA', 'KRITINUT',
    'KRN', 'KRONOX', 'KROSS', 'KRSNAA', 'KRYSTAL', 'KSB',
    'KSCL', 'KSHINTL', 'KSHITIJPOL', 'KSL', 'KSOLVES', 'KSR',
    'KTKBANK', 'KUANTUM', 'KUSUMGAR', 'KWIL', 'LAGNAM', 'LAHOTIOV',
    'LAKPRE', 'LAL', 'LALPATHLAB', 'LAMBODHARA', 'LANCORHOL', 'LANDMARK',
    'LANDSMILL', 'LAOPALA', 'LASA', 'LASERPOWER', 'LATENTVIEW', 'LATTEYS',
    'LAURUSLABS', 'LAXMICOT', 'LAXMIDENTL', 'LAXMIINDIA', 'LCCINFOTEC', 'LCL',
    'LEAPIND', 'LEMERITE', 'LEMONTREE', 'LENSKART', 'LEXUS', 'LFIC',
    'LGBBROSLTD', 'LGEINDIA', 'LGHL', 'LIBAS', 'LIBERTSHOE', 'LICHSGFIN',
    'LICI', 'LIKHITHA', 'LINC', 'LINCOLN', 'LINDEINDIA', 'LLOYDSENGG',
    'LLOYDSENT', 'LLOYDSME', 'LMW', 'LODHA', 'LOKESHMACH', 'LORDSCHLO',
    'LOTUSDEV', 'LOTUSEYE', 'LOVABLE', 'LOYALTEX', 'LPDC', 'LT',
    'LTF', 'LTFOODS', 'LTM', 'LTTS', 'LUMAXIND', 'LUMAXTECH',
    'LUPIN', 'LUXIND', 'LXCHEM', 'LYKALABS', 'M&M', 'M&MFIN',
    'MAANALU', 'MACPOWER', 'MADHAV', 'MADHAVIPL', 'MADHUCON', 'MADRASFERT',
    'MAFATIND', 'MAGADSUGAR', 'MAGNUM', 'MAHABANK', 'MAHAPEXLTD', 'MAHASTEEL',
    'MAHEPC', 'MAHESHWARI', 'MAHLIFE', 'MAHLOG', 'MAHSCOOTER', 'MAHSEAMLES',
    'MAITHANALL', 'MAJESAUT', 'MALLCOM', 'MALUPAPER', 'MAMATA', 'MANAKALUCO',
    'MANAKCOAT', 'MANAKSIA', 'MANAKSTEEL', 'MANALIPETC', 'MANAPPURAM', 'MANBA',
    'MANCREDIT', 'MANGALAM', 'MANGLMCEM', 'MANINDS', 'MANINFRA', 'MANIPALHOS',
    'MANKIND', 'MANOMAY', 'MANORAMA', 'MANORG', 'MANUGRAPH', 'MANYAVAR',
    'MAPMYINDIA', 'MARALOVER', 'MARATHON', 'MARICO', 'MARINE', 'MARKOLINES',
    'MARKSANS', 'MARSONS', 'MARUTI', 'MASFIN', 'MASKINVEST', 'MASTEK',
    'MASTERTR', 'MATRIMONY', 'MAWANASUG', 'MAXESTATES', 'MAXHEALTH', 'MAXIND',
    'MAYURUNIQ', 'MAZDA', 'MAZDOCK', 'MBAPL', 'MBEL', 'MBLINFRA',
    'MCCHRLS-B', 'MCL', 'MCLEODRUSS', 'MCLOUD', 'MCX', 'MEDANTA',
    'MEDIASSIST', 'MEDICAMEQ', 'MEDICO', 'MEDPLUS', 'MEESHO', 'MEGASTAR',
    'MEIL', 'MENNPIS', 'MENONBE', 'MEP', 'MERCANTILE', 'MERCURYEV',
    'METROBRAND', 'METROGLOBL', 'METROPOLIS', 'MFML', 'MFSL', 'MGEL',
    'MGL', 'MHLXMIRU', 'MHRIL', 'MICEL', 'MIDHANI', 'MIDWESTLTD',
    'MINDACORP', 'MINDTECK', 'MIRZAINT', 'MITCON', 'MITTAL', 'MKPL',
    'MMFL', 'MMP', 'MMTC', 'MMWL', 'MOBIKWIK', 'MODINATUR',
    'MODIRUBBER', 'MODIS', 'MODISONLTD', 'MODTHREAD', 'MOHITIND', 'MOIL',
    'MOKSH', 'MOL', 'MOLDTECH', 'MOLDTKPAC', 'MONARCH', 'MONEYBOXX',
    'MONTECARLO', 'MOREPENLAB', 'MOSCHIP', 'MOTHERSON', 'MOTILALOFS', 'MOTISONS',
    'MOTOGENFIN', 'MPHASIS', 'MPSLTD', 'MRF', 'MRPL', 'MSPL',
    'MSTCLTD', 'MSUMI', 'MTARTECH', 'MTEDUCARE', 'MTNL', 'MUFIN',
    'MUFTI', 'MUKANDLTD', 'MUKKA', 'MUKTAARTS', 'MUNJALAU', 'MUNJALSHOW',
    'MURUDCERA', 'MUTHOOTCAP', 'MUTHOOTFIN', 'MUTHOOTMF', 'MVELECTRO', 'MVGJL',
    'MWL', 'NACLIND', 'NAGREEKCAP', 'NAGREEKEXP', 'NAHARCAP', 'NAHARINDUS',
    'NAHARPOLY', 'NAHARSPING', 'NAM-INDIA', 'NARMADA', 'NATCAPSUQ', 'NATCOPHARM',
    'NATHBIOGEN', 'NATIONALUM', 'NATIONSTD', 'NAUKRI', 'NAVA', 'NAVINFLUOR',
    'NAVKARCORP', 'NAVKARURB', 'NAVNETEDUL', 'NAZARA', 'NBCC', 'NBIFIN',
    'NCC', 'NCLIND', 'NDGL', 'NDL', 'NDLVENTURE', 'NDRAUTO',
    'NDTV', 'NEAGI', 'NECCLTD', 'NECLIFE', 'NELCAST', 'NELCO',
    'NEOGEN', 'NEPHROPLUS', 'NESCO', 'NESTLEIND', 'NETWEB', 'NETWORK18',
    'NEUEON', 'NEULANDLAB', 'NEWGEN', 'NEXTMEDIA', 'NFL', 'NGIL',
    'NGLFINE', 'NH', 'NHPC', 'NIACL', 'NIBE', 'NIBL',
    'NIITLTD', 'NIITMTS', 'NILAINFRA', 'NILASPACES', 'NILE', 'NILKAMAL',
    'NIMBSPROJ', 'NINSYS', 'NIPPOBATRY', 'NIRAJ', 'NIRAJISPAT', 'NIRLON',
    'NITCO', 'NITINSPIN', 'NITIRAJ', 'NITTAGELA', 'NIVABUPA', 'NKIND',
    'NLCINDIA', 'NMDC', 'NOCIL', 'NOIDATOLL', 'NORBTEAEXP', 'NORTHARC',
    'NOVAAGRI', 'NOVARTIND', 'NPST', 'NRAIL', 'NRBBEARING', 'NRL',
    'NSIL', 'NSLNISP', 'NTPC', 'NTPCGREEN', 'NUCLEUS', 'NURECA',
    'NUVAMA', 'NUVOCO', 'NYKAA', 'OAL', 'OBCL', 'OBEROIRLTY',
    'OCCLLTD', 'ODIGMA', 'OFSS', 'OIL', 'OILCOUNTUB', 'OLAELEC',
    'OLECTRA', 'OMAXAUTO', 'OMAXE', 'OMFREIGHT', 'OMINFRAL', 'OMNI',
    'OMPOWER', 'ONELIFECAP', 'ONEPOINT', 'ONESOURCE', 'ONGC', 'ONIDA',
    'ONMOBILE', 'ONWARDTEC', 'OPTIEMUS', 'ORBTEXP', 'ORCHASP', 'ORCHPHARMA',
    'ORICONENT', 'ORIENTALTL', 'ORIENTBELL', 'ORIENTCEM', 'ORIENTCER', 'ORIENTELEC',
    'ORIENTHOT', 'ORIENTLTD', 'ORIENTPPR', 'ORIENTTECH', 'ORISSAMINE', 'ORKLAINDIA',
    'ORTINGLOBE', 'OSIAHYPER', 'OSWALAGRO', 'OSWALGREEN', 'OSWALPUMPS', 'OSWALSEEDS',
    'PACEDIGITK', 'PAGEIND', 'PAISALO', 'PAKKA', 'PALASHSECU', 'PALREDTEC',
    'PANACEABIO', 'PANACHE', 'PANAMAPET', 'PANSARI', 'PAR', 'PARACABLES',
    'PARADEEP', 'PARAGMILK', 'PARAS', 'PARASPETRO', 'PARKHOSPS', 'PARKHOTELS',
    'PARSVNATH', 'PASHUPATI', 'PASUPTAC', 'PATANJALI', 'PATELENG', 'PATELRMART',
    'PATINTLOG', 'PAUSHAKLTD', 'PAVNAIND', 'PAYTM', 'PCBL', 'PCJEWELLER',
    'PDMJEPAPER', 'PDSL', 'PEARLPOLY', 'PENIND', 'PENINLAND', 'PERSISTENT',
    'PETRONET', 'PFC', 'PFIZER', 'PFOCUS', 'PFS', 'PGEL',
    'PGHH', 'PGHL', 'PGIL', 'PHOENIXLTD', 'PICCADIL', 'PIDILITIND',
    'PIGL', 'PIIND', 'PILANIINVS', 'PILITA', 'PINELABS', 'PIONEEREMB',
    'PIONRINV', 'PIRAMALFIN', 'PITTIENG', 'PIXTRANS', 'PKTEA', 'PLASTIBLEN',
    'PLATIND', 'PLAZACABLE', 'PML', 'PNB', 'PNBGILTS', 'PNBHOUSING',
    'PNC', 'PNCINFRA', 'PNGJL', 'PNGSREVA', 'POCL', 'PODDARMENT',
    'POKARNA', 'POLICYBZR', 'POLYCAB', 'POLYMED', 'POLYPLEX', 'PONNIERODE',
    'POONAWALLA', 'POWERGRID', 'POWERICA', 'POWERINDIA', 'POWERMECH', 'PPAP',
    'PPL', 'PPLPHARMA', 'PRABHA', 'PRADPME', 'PRAENG', 'PRAJIND',
    'PRAKASH', 'PRAKASHSTL', 'PRAVEG', 'PRAXIS', 'PRECAM', 'PRECOT',
    'PRECWIRE', 'PREMCO', 'PREMEXPLN', 'PREMIER', 'PREMIERENE', 'PREMIERPOL',
    'PRESTIGE', 'PRICOLLTD', 'PRIMESECU', 'PRIMO', 'PRINCEPIPE', 'PRITI',
    'PRITIKAUTO', 'PRIVISCL', 'PROSTARM', 'PROTEAN', 'PROZONER', 'PRSMJOHNSN',
    'PRUDENT', 'PRUDMOULI', 'PSB', 'PSPPROJECT', 'PTC', 'PTCIL',
    'PTL', 'PUNJABCHEM', 'PURVA', 'PVP', 'PVRINOX', 'PVSL',
    'PWL', 'PYRAMID', 'QMSMEDI', 'QPOWER', 'QUADFUTURE', 'QUESS',
    'QUICKHEAL', 'QUINT', 'RACE', 'RACLGEAR', 'RADAAN', 'RADHIKAJWE',
    'RADIANTCMS', 'RADICO', 'RADIOCITY', 'RAILTEL', 'RAIN', 'RAINBOW',
    'RAJESHEXPO', 'RAJMET', 'RAJOOENG', 'RAJPALAYAM', 'RAJRATAN', 'RAJRILTD',
    'RAJSREESUG', 'RAJTV', 'RALLIS', 'RAMANEWS', 'RAMAPHO', 'RAMASTEEL',
    'RAMBHAJO', 'RAMCOCEM', 'RAMCOIND', 'RAMCOSYS', 'RAMKY', 'RAMRAT',
    'RANASUG', 'RANEHOLDIN', 'RATEGAIN', 'RATNAMANI', 'RATNAVEER', 'RAYMOND',
    'RAYMONDLSL', 'RAYMONDREL', 'RBA', 'RBLBANK', 'RBZJEWEL', 'RCF',
    'RCOM', 'RECLTD', 'REDINGTON', 'REDTAPE', 'REFEX', 'REGAAL',
    'REGENCERAM', 'RELAXO', 'RELCHEMQ', 'RELIABLE', 'RELIANCE', 'RELIGARE',
    'RELINFRA', 'RELTD', 'REMSONSIND', 'RENUKA', 'REPCOHOME', 'REPL',
    'REPRO', 'RESPONIND', 'RETAIL', 'RGL', 'RHETAN', 'RHFL',
    'RHIM', 'RHL', 'RICOAUTO', 'RIIL', 'RIR', 'RISHABH',
    'RITCO', 'RITES', 'RKDL', 'RKEC', 'RKFORGE', 'RKSWAMY',
    'RMC', 'RMDRIP', 'RML', 'RNBDENIMS', 'ROHLTD', 'ROLEXRINGS',
    'ROLLT', 'ROML', 'ROSSARI', 'ROSSELLIND', 'ROSSTECH', 'ROTO',
    'ROUTE', 'RPEL', 'RPGLIFE', 'RPOWER', 'RPPINFRA', 'RPPL',
    'RPSGVENT', 'RPTECH', 'RRIL', 'RRKABEL', 'RSDFIN', 'RSL',
    'RSSOFTWARE', 'RSWM', 'RSYSTEMS', 'RTNINDIA', 'RTNPOWER', 'RUBFILA',
    'RUBICON', 'RUBYMILLS', 'RUCHINFRA', 'RUCHIRA', 'RUDRA', 'RUPA',
    'RUSHIL', 'RUSTOMJEE', 'RVHL', 'RVNL', 'RVTH', 'S&SPOWER',
    'SAATVIKGL', 'SADBHAV', 'SADBHIN', 'SADHNANIQ', 'SAFARI', 'SAGARDEEP',
    'SAGCEM', 'SAGILITY', 'SAHLIBHFI', 'SAHYADRI', 'SAIL', 'SAILIFE',
    'SAIPARENT', 'SAKAR', 'SAKHTISUG', 'SAKSOFT', 'SAKUMA', 'SALASAR',
    'SALONA', 'SALSTEEL', 'SALZERELEC', 'SAMBHAAV', 'SAMBHV', 'SAMHI',
    'SAMMAANCAP', 'SAMPANN', 'SANATHAN', 'SANDESH', 'SANDHAR', 'SANDUMA',
    'SANGAMIND', 'SANGHVIMOV', 'SANGINITA', 'SANOFI', 'SANOFICONR', 'SANSERA',
    'SANSTAR', 'SANWARIA', 'SAPPHIRE', 'SAPPL', 'SARDAEN', 'SAREGAMA',
    'SARLAPOLY', 'SARVESHWAR', 'SASKEN', 'SATIA', 'SATIN', 'SAURASHCEM',
    'SAYAJIHOTL', 'SBC', 'SBCL', 'SBFC', 'SBGLP', 'SBICARD',
    'SBIFUNDS', 'SBILIFE', 'SBIN', 'SCANSTL', 'SCHAEFFLER', 'SCHAND',
    'SCHNEIDER', 'SCI', 'SCILAL', 'SCODATUBES', 'SCPL', 'SDBL',
    'SEAMECLTD', 'SECMARK', 'SECURKLOUD', 'SEDEMAC', 'SEIL', 'SEJALLTD',
    'SELMC', 'SEMAC', 'SENCO', 'SENORES', 'SEPC', 'SERVOTECH',
    'SESHAPAPER', 'SETCO', 'SETL', 'SETUINFRA', 'SEYAIND', 'SFL',
    'SGFIN', 'SGIL', 'SGL', 'SGMART', 'SHADOWFAX', 'SHAH',
    'SHAHALLOYS', 'SHAILY', 'SHAKTIPUMP', 'SHALBY', 'SHALPAINTS', 'SHANKARA',
    'SHANTI', 'SHANTI-RE', 'SHANTIGEAR', 'SHANTIGOLD', 'SHARDACROP', 'SHARDAMOTR',
    'SHARDUL', 'SHAREINDIA', 'SHBAJRG', 'SHEKHAWATI', 'SHEMAROO', 'SHILCTECH',
    'SHILPAMED', 'SHINDL', 'SHIVALIK', 'SHIVAMAUTO', 'SHIVAMILLS', 'SHIVATEX',
    'SHIVAUM', 'SHK', 'SHOPERSTOP', 'SHRADHA', 'SHREDIGCEM', 'SHREECEM',
    'SHREEJISPG', 'SHREEPUSHK', 'SHREERAMA', 'SHRENIK', 'SHREYANIND', 'SHRIKRISH',
    'SHRINGARMS', 'SHRIPISTON', 'SHRIRAMFIN', 'SHRIRAMPPS', 'SHUKRAPHAR', 'SHYAMCENT',
    'SHYAMMETL', 'SHYAMTEL', 'SICAGEN', 'SICALLOG', 'SIEMENS', 'SIGACHI',
    'SIGIND', 'SIGMA', 'SIGMAADV', 'SIGNATURE', 'SIGNPOST', 'SIKA',
    'SIKKO', 'SIL', 'SILGO', 'SILINV', 'SILLYMONKS', 'SILVERTUC',
    'SIMBHALS', 'SIMPLEXINF', 'SINCLAIR', 'SINDHUTRAD', 'SINGERIND', 'SINTERCOM',
    'SIRCA', 'SIS', 'SITINET', 'SIYSIL', 'SJS', 'SJVN',
    'SKFINDIA', 'SKFINDUS', 'SKIPPER', 'SKMEGGPROD', 'SKYGOLD', 'SMARTLINK',
    'SMARTWORKS', 'SMCGLOBAL', 'SMLMAH', 'SMLT', 'SMSPHARMA', 'SNOWMAN',
    'SOBHA', 'SOFTTECH', 'SOLARA', 'SOLARINDS', 'SOLARWORLD', 'SOLEX',
    'SOMANYCERA', 'SOMATEX', 'SOMICONVEY', 'SONACOMS', 'SONAL', 'SONAMLTD',
    'SONATSOFTW', 'SOTL', 'SOUTHBANK', 'SOUTHWEST', 'SPAL', 'SPANDANA',
    'SPARC', 'SPCENET', 'SPECIALITY', 'SPECTRUM', 'SPENCERS', 'SPIC',
    'SPLIL', 'SPLPETRO', 'SPMLINFRA', 'SPORTKING', 'SRD', 'SREEL',
    'SRF', 'SRGHFL', 'SRHHYPOLTD', 'SRM', 'SRTL', 'SSDL',
    'SSWL', 'STALLION', 'STANLEY', 'STAR', 'STARCEMENT', 'STARHEALTH',
    'STARPAPER', 'STARTECK', 'STCINDIA', 'STEELCAS', 'STEELCITY', 'STEELXIND',
    'STEL', 'STERTOOLS', 'STLNETWORK', 'STLTECH', 'STOVEKRAFT', 'STUDDS',
    'STYL', 'STYLAMIND', 'STYLEBAAZA', 'STYRENIX', 'SUBEXLTD', 'SUBROS',
    'SUDARCOLOR', 'SUDARSCHEM', 'SUDEEPPHRM', 'SUKHJITS', 'SULA', 'SUMEETINDS',
    'SUMICHEM', 'SUMIT', 'SUMMITSEC', 'SUNCLAY', 'SUNDARAM', 'SUNDARMFIN',
    'SUNDRMBRAK', 'SUNDRMFAST', 'SUNDROP', 'SUNFLAG', 'SUNPHARMA', 'SUNTECK',
    'SUNTV', 'SUPERHOUSE', 'SUPERSPIN', 'SUPRAJIT', 'SUPREME', 'SUPREMEENG',
    'SUPREMEIND', 'SUPREMEINF', 'SUPRIYA', 'SURAJEST', 'SURAJLTD', 'SURAKSHA',
    'SURANASOL', 'SURANAT&P', 'SURYALA', 'SURYALAXMI', 'SURYAROSNI', 'SURYODAY',
    'SUTLEJTEX', 'SUVEN', 'SUVIDHAA', 'SUYOG', 'SUZLON', 'SVLL',
    'SVPGLOB', 'SWANCORP', 'SWANDEF', 'SWARAJ', 'SWARAJENG', 'SWELECTES',
    'SWIGGY', 'SWSOLAR', 'SYMPHONY', 'SYNCOMF', 'SYNGENE', 'SYRMA',
    'SYSTMTXC', 'TAALTECH', 'TAINWALCHM', 'TAJGVK', 'TAKE', 'TALBROAUTO',
    'TAMBOLIIN', 'TANLA', 'TARACHAND', 'TARAPUR', 'TARC', 'TARIL',
    'TARMAT', 'TARSONS', 'TASTYBITE', 'TATACAP', 'TATACHEM', 'TATACOMM',
    'TATACONSUM', 'TATAELXSI', 'TATAINVEST', 'TATAPOWER', 'TATASTEEL', 'TATATECH',
    'TATVA', 'TBOTEK', 'TBZ', 'TCC', 'TCI', 'TCIEXP',
    'TCIFINANCE', 'TCPLPACK', 'TCS', 'TDPOWERSYS', 'TEAMGTY', 'TEAMLEASE',
    'TECHM', 'TECHNOCRAF', 'TECHNOE', 'TECHNVISN', 'TECILCHEM', 'TEGA',
    'TEJASNET', 'TEMBO', 'TENNIND', 'TERASOFT', 'TEXINFRA', 'TEXMOPIPES',
    'TEXRAIL', 'TFCILTD', 'TFL', 'TGBHOTELS', 'THACKER', 'THAKDEV',
    'THANGAMAYL', 'THEINVEST', 'THEJO', 'THELEELA', 'THEMISMED', 'THERMAX',
    'THOMASCOOK', 'THOMASCOTT', 'THYROCARE', 'TI', 'TICL', 'TIGERLOGS',
    'TIIL', 'TIINDIA', 'TIJARIA', 'TIL', 'TIMETECHNO', 'TIMEX',
    'TIMKEN', 'TINNARUBR', 'TIPSFILMS', 'TIPSMUSIC', 'TIRUMALCHM', 'TIRUPATIFL',
    'TITAGARH', 'TITAN', 'TMB', 'TMCV', 'TMPV', 'TNPETRO',
    'TNPL', 'TNTELE', 'TOKYOPLAST', 'TOLINS', 'TORNTPHARM', 'TORNTPOWER',
    'TOTAL', 'TOUCHWOOD', 'TPHQ', 'TPLPLASTEH', 'TRACXN', 'TRANSPEK',
    'TRANSRAILL', 'TRANSWORLD', 'TRAVELFOOD', 'TREEHOUSE', 'TREJHARA', 'TREL',
    'TRENT', 'TRF', 'TRIDENT', 'TRIGYN', 'TRITURBINE', 'TRIVENI',
    'TRU', 'TRUALT', 'TSFINV', 'TTKHLTCARE', 'TTKPRESTIG', 'TTL',
    'TTML', 'TURTLEMINT', 'TVSELECT', 'TVSHLTD', 'TVSMOTOR', 'TVSSCS',
    'TVSSRICHAK', 'TVTODAY', 'TVVISION', 'UBL', 'UCAL', 'UCOBANK',
    'UDS', 'UEL', 'UFBL', 'UFLEX', 'UFO', 'UGARSUGAR',
    'UGROCAP', 'UJJIVANSFB', 'ULTRACEMCO', 'ULTRAMAR', 'UMAEXPORTS', 'UMESLTD',
    'UMIYA-MRO', 'UNICHEMLAB', 'UNIDT', 'UNIECOM', 'UNIENTER', 'UNIINFO',
    'UNIMECH', 'UNIONBANK', 'UNIPARTS', 'UNITDSPR', 'UNITECH', 'UNITEDPOLY',
    'UNITEDTEA', 'UNIVASTU', 'UNIVCABLES', 'UNIVPHOTO', 'UNOMINDA', 'UPL',
    'URAVIDEF', 'URBANCO', 'URJA', 'USHAMART', 'USK', 'UTIAMC',
    'UTKARSHBNK', 'UTLSOLAR', 'UTTAMSUGAR', 'UYFINCORP', 'V2RETAIL', 'VADILALIND',
    'VAIBHAVGBL', 'VAISHALI', 'VAKRANGEE', 'VALIANTLAB', 'VALIANTORG', 'VAML',
    'VARDHACRLC', 'VARDMNPOLY', 'VARROC', 'VASCONEQ', 'VASWANI', 'VBL',
    'VCL', 'VEDL', 'VEDPOWER', 'VEEDOL', 'VELJAN', 'VENKEYS',
    'VENTIVE', 'VENUSPIPES', 'VENUSREM', 'VERANDA', 'VERTOZ', 'VESUVIUS',
    'VETO', 'VGL', 'VGUARD', 'VHL', 'VHLTD', 'VIDHIING',
    'VIDYAWIRES', 'VIJAYA', 'VIJIFIN', 'VIKASECO', 'VIKASLIFE', 'VIKRAMSOLR',
    'VIKRAN', 'VIMTALABS', 'VINATIORGA', 'VINCOFE', 'VINDHYATEL', 'VINEETLAB',
    'VINNY', 'VINYLINDIA', 'VIPCLOTHNG', 'VIPIND', 'VIPULLTD', 'VIRINCHI',
    'VISACHROME', 'VISAKAIND', 'VISHNU', 'VISHWARAJ', 'VISL', 'VITAL',
    'VIVIANA', 'VIVIDHA', 'VIVIMEDLAB', 'VIYASH', 'VLEGOV', 'VLSFINANCE',
    'VMART', 'VMM', 'VMSTMT', 'VOEPL', 'VOGL', 'VOLTAMP',
    'VOLTAS', 'VPRPL', 'VRAJ', 'VRLLOG', 'VSSL', 'VSTIND',
    'VSTL', 'VSTTILLERS', 'VTL', 'WAAREEENER', 'WAAREEINDO', 'WAAREERTL',
    'WABAG', 'WAKEFIT', 'WALCHANNAG', 'WANBURY', 'WCIL', 'WEALTH',
    'WEBELSOLAR', 'WEIZMANIND', 'WEL', 'WELCORP', 'WELENT', 'WELINV',
    'WELSPLSOL', 'WELSPUNLIV', 'WENDT', 'WESTLIFE', 'WEWIN', 'WEWORK',
    'WHEELS', 'WHIRLPOOL', 'WILLAMAGOR', 'WINDLAS', 'WINDMACHIN', 'WIPL',
    'WIPRO', 'WOCKPHARMA', 'WONDERLA', 'WORTHPERI', 'WPIL', 'WSI',
    'WSTCSTPAPR', 'XCHANGING', 'XELPMOC', 'XPROINDIA', 'XTGLOBAL', 'XTRANET',
    'YASHO', 'YATHARTH', 'YATRA', 'YESBANK', 'YUKEN', 'ZAGGLE',
    'ZEEL', 'ZEELEARN', 'ZEEMEDIA', 'ZENITHEXPO', 'ZENITHSTL', 'ZENSARTECH',
    'ZENTEC', 'ZFCVINDIA', 'ZFSTEERING', 'ZIMLAB', 'ZODIAC', 'ZODIACLOTH',
    'ZOTA', 'ZSARACOM', 'ZUARI', 'ZUARIIND', 'ZYDUSLIFE', 'ZYDUSWELL',
]

MAX_WEEKLY_HISTORY_DAYS = 180
ATR_PERIOD = 10
FACTOR = 3.0


def _normalize_access_token(token_value):
    if isinstance(token_value, str):
        token = token_value.strip()
        if token:
            return token
        raise ValueError("Empty access token")
    if isinstance(token_value, dict):
        for key in ("access_token", "token", "jwt", "value"):
            val = token_value.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        for nested in (token_value.get("data"), token_value.get("payload")):
            if isinstance(nested, dict):
                return _normalize_access_token(nested)
    if isinstance(token_value, (list, tuple)):
        for item in token_value:
            try:
                return _normalize_access_token(item)
            except Exception:
                continue
    raise ValueError("Could not locate an access token in the Groww response")


def _extract_candle_rows(raw_response):
    if isinstance(raw_response, dict):
        for key in ("candles", "data", "payload"):
            value = raw_response.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                nested = _extract_candle_rows(value)
                if nested is not None:
                    return nested
    elif isinstance(raw_response, list):
        return raw_response
    return None


def _build_dataframe(candles):
    if not candles:
        return pd.DataFrame()

    n = len(candles[0])
    if n == 7:
        cols = ["date", "open", "high", "low", "close", "volume", "oi"]
    elif n == 6:
        cols = ["date", "open", "high", "low", "close", "volume"]
    else:
        raise ValueError(f"Unexpected weekly candle column count: {n}")

    df = pd.DataFrame(candles, columns=cols)
    df["date"] = pd.to_datetime(df["date"])
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["date", "open", "high", "low", "close"]).sort_values("date").reset_index(drop=True)


api_key, api_secret = load_groww_credentials()
if not api_key or not api_secret:
    print("[ERROR] Missing Groww credentials. Set GROWW_API_KEY and GROWW_API_SECRET in the environment or create a .env file in the project root.")
    raise SystemExit(1)

try:
    raw_access_token = GrowwAPI.get_access_token(
        api_key=api_key,
        secret=api_secret,
    )
    access_token = _normalize_access_token(raw_access_token)
    growwapi = GrowwAPI(access_token)
except Exception as exc:  # pragma: no cover - credentials / runtime bootstrap path
    print(f"[ERROR] Groww authentication failed for weekly scanner: {exc}")
    raise SystemExit(1)


def safe_get_weekly_data(symbol: str):
    """Fetch just enough recent weekly OHLC data for the Supertrend signal."""
    try:
        exchange = getattr(growwapi, "EXCHANGE_NSE")
        segment = getattr(growwapi, "SEGMENT_CASH")
        groww_symbol = symbol if symbol.startswith("NSE-") else f"NSE-{symbol}"

        end_time = datetime.now()
        start_time = end_time - timedelta(days=MAX_WEEKLY_HISTORY_DAYS)

        raw = growwapi.get_historical_candles(
            groww_symbol=groww_symbol,
            exchange=exchange,
            segment=segment,
            candle_interval="1week",
            start_time=start_time.strftime("%Y-%m-%d %H:%M:%S"),
            end_time=end_time.strftime("%Y-%m-%d %H:%M:%S"),
        )
        candles = _extract_candle_rows(raw)
        if not candles:
            print(f"[WARN] No weekly candle data returned for {symbol}")
            return None

        df = _build_dataframe(candles)
        if df.empty:
            print(f"[WARN] Weekly data frame empty for {symbol}")
            return None

        # Groww enforces a maximum lookback for 1-week candles. Keep only the recent bars
        # needed by the weekly Supertrend calculation to avoid extra stale history.
        return df.tail(max(30, ATR_PERIOD + 5))
    except Exception as exc:  # pragma: no cover - network / API failure path
        print(f"[ERROR] Failed to fetch weekly data for {symbol}: {exc}")
        return None


def wilder_rma(series: pd.Series, period: int):
    """Calculate Wilder's smoothed moving average, matching the NIFTY strategy."""
    result = [np.nan] * len(series)
    values = series.to_numpy()
    if period < 1 or len(values) < period:
        return pd.Series(result, index=series.index)

    initial_average = np.nanmean(values[:period])
    result[period - 1] = initial_average
    for index in range(period, len(values)):
        previous = result[index - 1] if not np.isnan(result[index - 1]) else initial_average
        result[index] = (previous * (period - 1) + values[index]) / period
    return pd.Series(result, index=series.index)


def calculate_supertrend(df: pd.DataFrame):
    """Calculate weekly Supertrend and return only direction-flip signals."""
    if df is None or df.empty:
        return None, None

    work = df.copy()
    work["High"] = pd.to_numeric(work["high"], errors="coerce")
    work["Low"] = pd.to_numeric(work["low"], errors="coerce")
    work["Close"] = pd.to_numeric(work["close"], errors="coerce")
    work = work.dropna(subset=["High", "Low", "Close"]).reset_index(drop=True)
    if len(work) < ATR_PERIOD + 2:
        return None, None

    hl2 = (work["High"] + work["Low"]) / 2
    true_range = pd.concat(
        [
            work["High"] - work["Low"],
            (work["High"] - work["Close"].shift()).abs(),
            (work["Low"] - work["Close"].shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = wilder_rma(true_range, ATR_PERIOD)
    basic_upperband = hl2 + FACTOR * atr
    basic_lowerband = hl2 - FACTOR * atr
    final_upperband = np.full(len(work), np.nan)
    final_lowerband = np.full(len(work), np.nan)
    supertrend_values = np.full(len(work), np.nan)
    direction = np.full(len(work), -1)
    close = work["Close"].to_numpy()
    atr_values = atr.to_numpy()
    upper_values = basic_upperband.to_numpy()
    lower_values = basic_lowerband.to_numpy()

    for index in range(len(work)):
        if np.isnan(atr_values[index]):
            continue
        if index == 0 or np.isnan(atr_values[index - 1]):
            final_upperband[index] = upper_values[index]
            final_lowerband[index] = lower_values[index]
            supertrend_values[index] = final_upperband[index]
            continue

        previous_close = close[index - 1]
        final_upperband[index] = (
            upper_values[index]
            if upper_values[index] < final_upperband[index - 1]
            or previous_close > final_upperband[index - 1]
            else final_upperband[index - 1]
        )
        final_lowerband[index] = (
            lower_values[index]
            if lower_values[index] > final_lowerband[index - 1]
            or previous_close < final_lowerband[index - 1]
            else final_lowerband[index - 1]
        )

        if direction[index - 1] == -1:
            direction[index] = 1 if close[index] > final_upperband[index] else -1
        else:
            direction[index] = -1 if close[index] < final_lowerband[index] else 1
        supertrend_values[index] = (
            final_lowerband[index] if direction[index] == 1 else final_upperband[index]
        )

    work["Supertrend"] = supertrend_values
    work["Direction"] = direction
    last = work.iloc[-1]
    previous = work.iloc[-2]
    signal = None
    if pd.notna(last["Supertrend"]) and pd.notna(previous["Supertrend"]):
        if last["Direction"] == 1 and previous["Direction"] == -1:
            signal = "BUY"
        elif last["Direction"] == -1 and previous["Direction"] == 1:
            signal = "SELL"

    return signal, last


def emit_signal(symbol: str, side: str, price: float, reason: str, time_value: datetime | str):
    """Emit a scheduler-readable trade signal."""
    if isinstance(time_value, datetime):
        signal_time = time_value.strftime("%Y-%m-%dT%H:%M:%S")
    else:
        signal_time = str(time_value)

    print(
        "[TRADE SIGNAL] "
        f"Script=SupperWeekScan Side={side} Time={signal_time} Symbol={symbol} "
        f"Strike=NA Price={float(price):.2f} "
        f"Reason={reason} Lots=1 LotSize=1 Qty=1 OrderPlaced=YES"
    )


def scan_weekly_supertrend():
    """Scan the watchlist and return any weekly BUY/SELL signals."""
    results = []
    total = len(WATCHLIST)
    for index, symbol in enumerate(WATCHLIST, start=1):
        print(f"[SCAN] {index}/{total} {symbol} | FETCHING", flush=True)
        data = safe_get_weekly_data(symbol)
        if data is None or data.empty:
            print(
                f"[SCAN] {index}/{total} {symbol} | PRICE=NA | SUPERTREND=NA | SIGNAL=NO_DATA",
                flush=True,
            )
            continue

        signal, latest = calculate_supertrend(data)
        if latest is None:
            print(
                f"[SCAN] {index}/{total} {symbol} | PRICE=NA | SUPERTREND=NA | SIGNAL=INSUFFICIENT_DATA",
                flush=True,
            )
            continue

        price = latest.get("Close")
        st_value = latest.get("Supertrend")
        direction_text = "UP" if latest.get("Direction") == 1 else "DOWN"
        price_text = f"{float(price):.2f}" if pd.notna(price) else "NA"
        st_text = f"{float(st_value):.2f}" if pd.notna(st_value) else "NA"
        signal_text = signal or "NO_SIGNAL"
        print(
            f"[SCAN] {index}/{total} {symbol} | PRICE={price_text} | "
            f"SUPERTREND={st_text} | DIRECTION={direction_text} | SIGNAL={signal_text}",
            flush=True,
        )

        if signal is None:
            continue

        reason = f"Weekly Supertrend flip {signal} on {symbol}"
        price = float(price)
        signal_time = pd.Timestamp(latest["date"]).to_pydatetime()
        results.append((symbol, signal, price, reason, signal_time))

    return results


def main():
    parser = argparse.ArgumentParser(description="Weekly supertrend stock scanner")
    parser.add_argument("--dry-run", action="store_true", help="Print signals only without trading logic")
    args = parser.parse_args()

    if not WATCHLIST:
        print(
            "[ERROR] WATCHLIST is empty. Add symbols to WATCHLIST in SupperWeekScan.py.",
            flush=True,
        )
        return 2

    print(f"[SCAN] Starting weekly supertrend scan for {len(WATCHLIST)} symbols")
    triggers = scan_weekly_supertrend()

    if not triggers:
        print("[SCAN] No weekly BUY/SELL supertrend trigger found for the current watchlist.")
        return 0

    for symbol, side, price, reason, signal_time in triggers:
        emit_signal(symbol, side, price, reason, signal_time)

    print(f"[SCAN] Found {len(triggers)} weekly signal(s).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("[SCAN] Weekly scanner interrupted by user.")
        raise SystemExit(130)
