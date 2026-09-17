"""Text helpers: HTML stripping, Dutch location checks, language labels, dates."""
import html
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser


# ---------------------------------------------------------------- HTML text

class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "template"}
    BREAKS = {"br", "p", "li", "div", "tr", "h1", "h2", "h3", "h4", "ul", "ol"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip_depth += 1
        elif tag in self.BREAKS:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self.BREAKS:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(data)


def strip_html(markup):
    if not markup:
        return ""
    # Some APIs (Greenhouse) return HTML that is itself entity-escaped.
    if "&lt;" in markup and "<" not in markup:
        markup = html.unescape(markup)
    parser = _TextExtractor()
    parser.feed(markup)
    return re.sub(r"\s+", " ", "".join(parser.parts)).strip()


def fold(text):
    """Lowercase and remove accents so 'Coördinator' matches 'coordinator'."""
    decomposed = unicodedata.normalize("NFD", text or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


# ---------------------------------------------------------------- locations

NL_TERMS = [
    "netherlands", "nederland", "holland", "the hague", "den haag", "'s-hertogenbosch",
    "s-hertogenbosch", "den bosch",
]
NL_CITIES = [
    "amsterdam", "rotterdam", "schiphol", "venlo", "venray", "tilburg", "eindhoven", "utrecht",
    "oosterhout", "breda", "moerdijk", "roosendaal", "nijmegen", "waalwijk", "zwolle", "almere",
    "hoofddorp", "amersfoort", "arnhem", "apeldoorn", "groningen", "maastricht", "heerlen",
    "sittard", "roermond", "weert", "helmond", "veghel", "uden", "oss", "duiven", "nieuwegein",
    "zaandam", "barendrecht", "ridderkerk", "botlek", "maasvlakte", "europoort", "vlissingen",
    "terneuzen", "emmen", "hengelo", "enschede", "almelo", "zeewolde", "lelystad", "bleiswijk",
    "zoetermeer", "delft", "leiden", "haarlem", "rozenburg", "spijkenisse", "dordrecht",
    "gorinchem", "tiel", "culemborg", "wageningen", "doetinchem", "winterswijk",
    "coevorden", "heerenveen", "leeuwarden", "harderwijk", "nieuw-vennep", "hazeldonk",
    "etten-leur", "raamsdonksveer", "waddinxveen", "alphen aan den rijn", "gouda",
    "bergen op zoom", "milsbeek", "kerkrade", "born", "echt", "bladel", "oirschot", "tholen",
    "vianen", "houten", "woerden", "hendrik-ido-ambacht", "zwijndrecht", "alblasserdam",
    "heteren", "nieuwkuijk", "son en breugel", "veldhoven", "cuijk", "boxmeer",
    "ede", "geldermalsen", "bunschoten", "hoogeveen", "meppel", "assen", "deventer",
    "zutphen", "oldenzaal", "wijchen", "naaldwijk", "aalsmeer", "rijnsburg", "uithoorn",
    "badhoevedorp", "diemen", "amstelveen", "velsen", "ijmuiden", "beverwijk", "den helder",
    "hoorn", "purmerend", "geleen",
]
FOREIGN_TERMS = [
    "germany", "deutschland", "belgium", "belgie", "belgique", "france", "united kingdom",
    "england", "scotland", "ireland", "poland", "polska", "spain", "espana", "italy", "italia",
    "czech", "slovakia", "hungary", "romania", "bulgaria", "greece", "sweden", "denmark",
    "norway", "finland", "austria", "switzerland", "portugal", "luxembourg", "lithuania",
    "latvia", "estonia", "croatia", "slovenia", "serbia", "turkey", "turkiye", "united states",
    "usa", "canada", "mexico", "brazil", "argentina", "chile", "china", "hong kong", "taiwan",
    "japan", "korea", "india", "singapore", "malaysia", "vietnam", "thailand", "indonesia",
    "philippines", "australia", "new zealand", "south africa", "egypt", "morocco", "uae",
    "united arab emirates", "dubai", "saudi", "qatar", "israel",
    "hamburg", "berlin", "munich", "frankfurt", "duisburg", "antwerp", "antwerpen", "brussels",
    "london", "paris", "warsaw", "madrid", "barcelona", "milan", "prague", "budapest",
    "istanbul", "stockholm", "copenhagen", "vienna", "zurich", "lisbon", "dublin",
]
_NL_CODE = re.compile(r"(^|[\s,(/\-])(nl|nld)($|[\s,)/\-])")
_TRAILING_CODE = re.compile(r",\s*([a-z]{2})\s*$")


def _contains_term(folded, term):
    return re.search(r"(^|[^a-z])" + re.escape(term) + r"($|[^a-z])", folded) is not None


def location_status(text):
    """Return 'nl', 'foreign' or 'unknown' for a free-text location."""
    folded = fold(text).strip()
    if not folded:
        return "unknown"
    if any(_contains_term(folded, t) for t in NL_TERMS + NL_CITIES) or _NL_CODE.search(folded):
        return "nl"
    if any(_contains_term(folded, t) for t in FOREIGN_TERMS):
        return "foreign"
    code = _TRAILING_CODE.search(folded)
    if code and code.group(1) != "nl":
        return "foreign"
    return "unknown"


def keep_location(text, strict):
    """Strict sources keep only clear Dutch locations; Dutch-only sites also keep unknowns."""
    status = location_status(text)
    return status == "nl" or (status == "unknown" and not strict)


# ---------------------------------------------------------------- language

_DUTCH_WORDS = {"de", "het", "een", "en", "van", "voor", "met", "je", "jij", "wij", "ons",
                "bij", "naar", "zijn", "wordt", "jouw", "onze", "als", "ook", "niet", "die"}
_ENGLISH_WORDS = {"the", "and", "for", "with", "you", "we", "our", "to", "of", "is", "are",
                  "your", "will", "in", "as", "this", "be", "on", "that", "an"}

_DUTCH_REQUIRED = [
    r"(fluent|native|excellent|good|strong|proficient|proficiency)\s+(command\s+of\s+|knowledge\s+of\s+|level\s+of\s+|in\s+)?(the\s+)?dutch",
    r"dutch\s+(language\s+)?(skills\s+)?(is\s+|are\s+)?(required|mandatory|essential|a\s+must)",
    r"(speak|write|written\s+and\s+spoken)\s+(fluent\s+)?dutch",
    r"(uitstekende|goede|zeer\s+goede)\s+beheersing\s+van\s+de\s+nederlandse\s+taal",
    r"nederlands(e\s+taal)?\s+(en\s+engels\s+)?(in\s+woord\s+en\s+geschrift|vloeiend|vereist)",
    r"vloeiend\s+nederlands",
    r"(spreekt|beheerst)\s+(vloeiend\s+|goed\s+)?nederlands",
]
_DUTCH_OPTIONAL = re.compile(
    r"(\bplus\b|advantage|nice\s+to\s+have|\bpre\b|preferred|pluspunt|is\s+een\s+pre|\basset\b)"
)


def written_in_dutch(text):
    """True if the ad text is mainly Dutch, None if there is too little text to judge."""
    words = re.findall(r"[a-z]+", fold(text))
    if len(words) < 40:
        return None
    dutch = sum(w in _DUTCH_WORDS for w in words)
    english = sum(w in _ENGLISH_WORDS for w in words)
    return dutch > english


def dutch_required(text):
    """True only when the ad explicitly asks for Dutch and does not call it optional."""
    folded = fold(text)
    if len(folded) < 80:
        return None
    for pattern in _DUTCH_REQUIRED:
        for match in re.finditer(pattern, folded):
            after = folded[match.end():match.end() + 45]
            if not _DUTCH_OPTIONAL.search(after):
                return True
    return False


# ---------------------------------------------------------------- dates

_TEXT_DATE_FORMATS = ["%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y", "%d-%m-%Y", "%d/%m/%Y"]


def normalize_date(value):
    """Convert ISO strings, epoch values, RFC 2822 and written dates to YYYY-MM-DD."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(seconds, tz=timezone.utc).date().isoformat()
    text = str(value).strip()
    iso = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
    if iso:
        return iso.group(1)
    try:
        return parsedate_to_datetime(text).date().isoformat()
    except (TypeError, ValueError, IndexError):
        pass
    for fmt in _TEXT_DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_relative_posted(text, today=None):
    """Workday style: 'Posted Today', 'Posted Yesterday', 'Posted 3 Days Ago'."""
    today = today or date.today()
    folded = fold(text)
    if "today" in folded:
        return today.isoformat()
    if "yesterday" in folded:
        return (today - timedelta(days=1)).isoformat()
    match = re.search(r"(\d+)\+?\s+days?\s+ago", folded)
    if match and "+" not in folded:
        return (today - timedelta(days=int(match.group(1)))).isoformat()
    return None
