"""Reproducible cosmetic names: 90% local traditions, 10% another country's pool.

The background draw and spelling draw are independent of skills, portraits, ages
and language. This is fictional name variety, not a model of real demographics.
"""

from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID

from goaletto.domain.names import NAME_POOLS, VIETNAMESE_FAMILIES, VIETNAMESE_GIVEN
from goaletto.domain.nationalities import COUNTRIES, nationality

PERSON_NAMING_POLICY = "nationality-names-v2"
POOLS = {key: (tuple(first.split()), tuple(last.split())) for key, first, last in NAME_POOLS}
EXTRA_POOLS = (
    (
        "ukrainian",
        "Andrii Oleksandr Dmytro Bohdan Mykola Oleh Taras Serhii",
        "Shevchenko Kovalenko Bondarenko Tkachenko Melnyk Kravchenko Kovalchuk Savchenko",
    ),
    (
        "belarusian",
        "Aliaksandr Aliaksei Andrei Artsiom Dzmitry Ihar Mikalai Siarhei",
        "Kavaliou Ivanou Kazlou Zaitsau Hryshkevich Vasileuski Karaliou Kavalchuk",
    ),
    (
        "dutch",
        "Daan Luuk Bram Lars Sven Thijs Wout Jeroen",
        "De-Jong De-Vries Bakker Visser Smit Meijer Mulder Bos",
    ),
    (
        "danish",
        "Mikkel Søren Rasmus Kasper Anders Niels Christian Frederik",
        "Jensen Nielsen Hansen Pedersen Andersen Christensen Larsen Sørensen",
    ),
    (
        "norwegian",
        "Magnus Ole Sander Eirik Håkon Kristoffer Henrik Vegard",
        "Hansen Johansen Olsen Larsen Andersen Nilsen Berg Haugen",
    ),
    (
        "swedish",
        "Erik Johan Emil Oskar Viktor Gustav Nils Alexander",
        "Andersson Johansson Karlsson Nilsson Eriksson Larsson Olsson Svensson",
    ),
    (
        "finnish",
        "Eero Jari Teemu Mikko Antti Juha Lauri Pekka",
        "Virtanen Korhonen Nieminen Mäkinen Hämäläinen Laine Heikkinen Koskinen",
    ),
    (
        "icelandic",
        "Jón Ari Einar Aron Björn Ólafur Sigurður Gunnar",
        "Jónsson Sigurðsson Guðmundsson Ólafsson Einarsson Arnarson Gunnarsson Magnússon",
    ),
    (
        "irish",
        "Liam Seán Cian Conor Finn Patrick Niall Eoin",
        "Murphy Kelly Byrne Walsh Ryan Sullivan Doyle McCarthy",
    ),
    (
        "croatian",
        "Ivan Ante Marin Josip Domagoj Lovro Borna Mateo",
        "Horvat Kovačić Babić Marić Novak Jurić Knežević Vuković",
    ),
    (
        "slovenian",
        "Jan Žan Miha Nejc Tadej Jaka Rok Blaž",
        "Novak Horvat Kovačič Krajnc Zupan Potočnik Kralj Vidmar",
    ),
    (
        "bosnian",
        "Edin Emir Mirza Haris Adnan Kenan Amar Faruk",
        "Hadžić Hodžić Delić Halilović Begović Jahić Osmanović Čaušević",
    ),
    (
        "macedonian",
        "Goran Ilija Nikola Aleksandar Darko Dejan Bojan Stefan",
        "Trajkovski Stojanovski Petrovski Nikolovski Ristovski Dimitrievski Jovanovski Kostovski",
    ),
    (
        "albanian",
        "Arben Bekim Dritan Erion Ilir Luan Besnik Alban",
        "Hoxha Shehu Dervishi Krasniqi Gashi Berisha Leka Kola",
    ),
    (
        "czech",
        "Petr Jan Tomáš Jakub Lukáš Jiří Martin Pavel",
        "Novák Svoboda Novotný Dvořák Černý Procházka Kučera Veselý",
    ),
    (
        "slovak",
        "Ján Michal Matej Martin Peter Lukáš Tomáš Šimon",
        "Horváth Kováč Varga Tóth Nagy Baláž Molnár Hudák",
    ),
    (
        "hungarian",
        "Ádám Bence Dániel Gábor László Péter Zoltán Márton",
        "Nagy Kovács Tóth Szabó Horváth Varga Kiss Molnár",
    ),
    (
        "romanian",
        "Andrei Alexandru Mihai Ștefan Radu Florin Vlad Cristian",
        "Popescu Ionescu Popa Dumitrescu Stan Stoica Rusu Munteanu",
    ),
    (
        "bulgarian",
        "Georgi Ivan Dimitar Nikolay Petar Aleksandar Stefan Todor",
        "Ivanov Georgiev Dimitrov Petrov Nikolov Stoyanov Todorov Iliev",
    ),
    (
        "greek",
        "Giorgos Dimitris Nikos Kostas Giannis Panagiotis Vasilis Alexandros",
        "Papadopoulos Nikolaidis Georgiadis Dimitriou Ioannidis Antoniou Christodoulou Pavlidis",
    ),
    (
        "egyptian",
        "Ahmed Mohamed Mahmoud Omar Mostafa Tarek Hassan Yasser",
        "Hassan Ibrahim Mahmoud Nasser Fathy Samir Mansour Soliman",
    ),
    (
        "cameroonian",
        "Jean-Paul Pierre Alain Samuel Patrick Christian André Vincent",
        "Moukoko Mvondo Nkono Mbarga Ebong Mballa Etoundi Ngono",
    ),
    (
        "persian",
        "Ali Reza Mehdi Hossein Amir Farhad Omid Saman",
        "Hosseini Mohammadi Ahmadi Karimi Rezaei Rahimi Azizi Nouri",
    ),
    (
        "saudi",
        "Abdullah Abdulrahman Fahad Nasser Yasser Khalid Salem Saud",
        "Al-Harbi Al-Qahtani Al-Dosari Al-Ghamdi Al-Otaibi Al-Shahrani Al-Mutairi Al-Zahrani",
    ),
    (
        "kazakh",
        "Aidos Alikhan Arman Aslan Bekzat Daniyar Nurlan Sanzhar",
        "Akhmetov Bekov Iskakov Nurgaliyev Sarsenov Tulegenov Zhumabayev Orazov",
    ),
    (
        "uzbek",
        "Aziz Diyor Jasur Sardor Shavkat Temur Ulugbek Otabek",
        "Abdullayev Karimov Ismoilov Rasulov Tursunov Yusupov Qodirov Usmonov",
    ),
    (
        "georgian",
        "Giorgi Davit Levan Luka Nika Irakli Zurab Vakhtang",
        "Beridze Kapanadze Gelashvili Maisuradze Lomidze Mchedlishvili Tsiklauri Kvaratskhelia",
    ),
    (
        "armenian",
        "Aram Armen Artur Davit Hayk Tigran Vardan Narek",
        "Sargsyan Grigoryan Harutyunyan Hovhannisyan Petrosyan Hakobyan Avetisyan Mkrtchyan",
    ),
    (
        "azerbaijani",
        "Ali Anar Elvin Emin Kamran Orkhan Rashad Vugar",
        "Aliyev Mammadov Huseynov Hasanov Ismayilov Guliyev Abbasov Ibrahimov",
    ),
    (
        "lithuanian",
        "Mantas Lukas Jonas Domantas Paulius Darius Tomas Rokas",
        "Kazlauskas Petrauskas Jankauskas Stankevičius Vasiliauskas Žukauskas Butkus Paulauskas",
    ),
    (
        "latvian",
        "Jānis Artūrs Edgars Kārlis Kristaps Mārtiņš Roberts Rihards",
        "Bērziņš Kalniņš Ozoliņš Jansons Liepiņš Krūmiņš Balodis Eglītis",
    ),
    (
        "estonian",
        "Andres Jaan Martin Markus Rasmus Siim Tõnis Karl",
        "Tamm Saar Sepp Mägi Kask Kukk Rebane Oja",
    ),
    (
        "ivorian",
        "Kouadio Koffi Yao Yacouba Seydou Amadou Franck Serge",
        "Koné Coulibaly Kouassi Yapi Traoré Bamba Diabaté N’Guessan",
    ),
    (
        "malian",
        "Adama Boubacar Cheick Ibrahim Lassana Mamadou Moussa Seydou",
        "Traoré Diarra Coulibaly Keïta Diallo Dembélé Sissoko Sidibé",
    ),
    (
        "burkinabe",
        "Issa Alain Bertrand Charles Edmond Frédéric Issouf Yacouba",
        "Ouédraogo Sawadogo Sanou Kaboré Zongo Compaoré Traoré Konaté",
    ),
    (
        "guinean",
        "Alpha Amadou Ibrahima Lamine Mamadou Naby Ousmane Sékou",
        "Diallo Camara Barry Bah Baldé Sylla Condé Touré",
    ),
)
POOLS.update({key: (tuple(first.split()), tuple(last.split())) for key, first, last in EXTRA_POOLS})
# Larger shared-language pools keep starting worlds unique without numeric suffixes.
for key, first, last in (
    (
        "english",
        "Daniel Benjamin Samuel Henry Ethan Lewis Callum Nathan",
        "Walker Hall Allen Young Wright King Scott Green",
    ),
    (
        "spanish",
        "Antonio Carlos Daniel David Fernando Jorge Luis Pablo",
        "Alonso Castro Delgado Herrera Molina Navarro Ortega Romero",
    ),
    (
        "portuguese",
        "Bruno Carlos Daniel Eduardo Fábio Henrique Miguel Rafael",
        "Barbosa Carvalho Fernandes Gomes Lopes Martins Rocha Sousa",
    ),
    (
        "french",
        "Alexandre Clément Hugo Maxime Nicolas Olivier Pierre Sébastien",
        "Roux Vincent Faure André Mercier Blanc Guérin Boyer",
    ),
):
    a, b = POOLS[key]
    POOLS[key] = a + tuple(first.split()), b + tuple(last.split())
POOLS["vietnamese"] = VIETNAMESE_FAMILIES, VIETNAMESE_GIVEN
COUNTRY_POOLS = {
    "AR": ("spanish",),
    "AU": ("english",),
    "BE": ("french", "dutch"),
    "BR": ("portuguese",),
    "CA": ("english", "french"),
    "CH": ("german", "french", "italian"),
    "CL": ("spanish",),
    "CM": ("cameroonian",),
    "CO": ("spanish",),
    "DE": ("german",),
    "DK": ("danish",),
    "DZ": ("arabic_maghreb",),
    "EG": ("egyptian",),
    "ES": ("spanish",),
    "FR": ("french",),
    "GB": ("english",),
    "GH": ("akan",),
    "HR": ("croatian",),
    "IT": ("italian",),
    "JP": ("japanese",),
    "KR": ("korean",),
    "MA": ("arabic_maghreb",),
    "MX": ("spanish",),
    "NG": ("yoruba", "igbo", "hausa"),
    "NL": ("dutch",),
    "NO": ("norwegian",),
    "PL": ("polish",),
    "PT": ("portuguese",),
    "RS": ("serbian",),
    "SE": ("swedish",),
    "SN": ("wolof",),
    "TR": ("turkish",),
    "RU": ("slavic",),
    "UA": ("ukrainian",),
    "BY": ("belarusian",),
    "AT": ("german",),
    "CZ": ("czech",),
    "SK": ("slovak",),
    "HU": ("hungarian",),
    "RO": ("romanian",),
    "BG": ("bulgarian",),
    "GR": ("greek",),
    "FI": ("finnish",),
    "IE": ("irish",),
    "IS": ("icelandic",),
    "SI": ("slovenian",),
    "BA": ("bosnian", "serbian", "croatian"),
    "ME": ("serbian",),
    "MK": ("macedonian",),
    "AL": ("albanian",),
    "CN": ("chinese",),
    "IN": ("hindi", "bengali"),
    "BD": ("bengali",),
    "TH": ("thai",),
    "ID": ("indonesian",),
    "VN": ("vietnamese",),
    "GM": ("mandinka",),
    "KE": ("kikuyu",),
    "ZA": ("zulu", "english"),
    "UY": ("spanish",),
    "PY": ("spanish",),
    "PE": ("spanish",),
    "EC": ("spanish",),
    "US": ("english",),
    "NZ": ("english",),
    "TN": ("arabic_maghreb",),
    "IR": ("persian",),
    "SA": ("saudi",),
    "KZ": ("kazakh",),
    "UZ": ("uzbek",),
    "GE": ("georgian",),
    "AM": ("armenian",),
    "MD": ("romanian",),
    "AZ": ("azerbaijani",),
    "LT": ("lithuanian",),
    "LV": ("latvian",),
    "EE": ("estonian",),
    "CI": ("ivorian",),
    "ML": ("malian",),
    "BF": ("burkinabe",),
    "GN": ("guinean",),
}
assert set(COUNTRY_POOLS) == set(COUNTRIES)


@dataclass(frozen=True)
class PersonName:
    name: str
    country: str
    origin: str
    tradition: str

    @property
    def migrant(self) -> bool:
        return self.country != self.origin


def _draw(person_id: UUID, label: str) -> int:
    return int.from_bytes(
        sha256(f"{PERSON_NAMING_POLICY}:{person_id}:{label}".encode()).digest(), "big"
    )


def generated_name(person_id: UUID, country: str | None = None, *, attempt: int = 0) -> PersonName:
    country = country or nationality(person_id)
    if country not in COUNTRY_POOLS or type(attempt) is not int or attempt < 0:
        raise ValueError("Unknown country or invalid name attempt")
    origin = country
    if _draw(person_id, "background") % 10 == 0:
        foreign = tuple(
            c for c in COUNTRIES if not set(COUNTRY_POOLS[c]) & set(COUNTRY_POOLS[country])
        )
        origin = foreign[_draw(person_id, "origin") % len(foreign)]
    traditions = COUNTRY_POOLS[origin]
    tradition = traditions[_draw(person_id, "tradition") % len(traditions)]
    first, last = POOLS[tradition]
    # A fixed cyclic walk allows a world to resolve collisions without changing
    # nationality, origin, skills or names already persisted for other people.
    index = (_draw(person_id, "spelling") + attempt) % (len(first) * len(last))
    a, b = first[index // len(last)], last[index % len(last)]
    return PersonName(f"{a} {b}", country, origin, tradition)


def person_name(person_id: UUID, country: str | None = None) -> str:
    return generated_name(person_id, country).name
