import json
from pathlib import Path
from typing import Dict, List, Tuple
import geopandas as gpd
import pandas as pd
from geopy.distance import geodesic
from jinja2 import Template
import osmnx as ox
# планировался дебаг мод но нам времени не хватило, мало слишком
# DEBUG_MODE = 'не сделан'
# переменные, которые нельзя менять (можно)
POPULATION_DATA: Dict[str, int] = {
    "Центральный район": 118798, "Куйбышевский район": 74569, "Заводской район": 92648,
    "Новоильинский район": 78270, "Кузнецкий район": 47817, "Орджоникидзевский район": 74628,
}
CHOROPLETH_COLORS: List[str] = ['#edf8fb', '#b3cde3', '#8c96c6', '#8856a7', '#810f7c']

class GeneratorKarty:
    # вся генерация тут
    def __init__(self, nazvanie_goroda: str, centr_karty: List[float], api_klyuch: str, prozrachnost: float):
        self.nazvanie_goroda = nazvanie_goroda
        self.centr_karty = centr_karty
        self.api_klyuch = api_klyuch
        self.prozrachnost = prozrachnost
        
        self.rayony_gdf = self._zapoluchit_rayony()
        self.shkoly_gdf, self.udo_gdf = self._zagruzit_csv()
        self._obrabotat_dannyie()
        self.statistika_html = self._sdelat_statistiku_html()

    def _zapoluchit_rayony(self) -> gpd.GeoDataFrame:
        print("Загрузка границ районов из OpenStreetMap...")
        try:
            # пробуем разные формулы
            for imya_funkcii in ['features_from_place', 'geometries_from_place', 'gdf_from_place']:
                if hasattr(ox, imya_funkcii):
                    vse_rayony_gdf = getattr(ox, imya_funkcii)(
                        f"{self.nazvanie_goroda}, Кемеровская область, Россия",
                        tags={'boundary': 'administrative', 'admin_level': '9'}
                    )
                    break
            else:
                raise RuntimeError("Не найдена подходящая функция для загрузки геометрий в osmnx.")

            vse_rayony_gdf = vse_rayony_gdf.reset_index()
            print(f"-> Найдено {len(vse_rayony_gdf)} административных единиц в OSM.")
            
            naidennye_imena = vse_rayony_gdf['name'].dropna().unique().tolist()
            print(f"-> Найденные названия районов в OSM: {naidennye_imena}")

            geodannyie = vse_rayony_gdf[vse_rayony_gdf['name'].isin(POPULATION_DATA.keys())].copy()
            if geodannyie.empty:
                raise ValueError("Не удалось сопоставить ни одного района из POPULATION_DATA.")

            # это не я их украл
            propavshie = set(POPULATION_DATA.keys()) - set(geodannyie['name'])
            if propavshie:
                print(f"\nПРЕДУПРЕЖДЕНИЕ: Не найдены районы: {list(propavshie)}\n")

            # лечим геометрию 
            geodannyie['geometry'] = geodannyie.geometry.buffer(0)
            geodannyie.dropna(subset=['geometry'], inplace=True)
            print(f"-> Успешно обработано {len(geodannyie)} районов.")

            geodannyie['population'] = geodannyie['name'].map(POPULATION_DATA)
            geodannyie['area_km2'] = geodannyie.to_crs(epsg=32644).area / 1_000_000
            geodannyie['pop_density'] = (geodannyie['population'] / geodannyie['area_km2']).round(2)
            
            return geodannyie[['name', 'geometry', 'population', 'area_km2', 'pop_density']]

        except Exception as e:
            print(f"КРИТИЧЕСКАЯ ОШИБКА при загрузке данных из OSM: {e}")
            raise

    def _prochitat_csv(self, put_k_failu: str, razdelitel: str, nuzhnye_stolbtsy: List[str], zapros: str = None) -> gpd.GeoDataFrame:
        put = Path(put_k_failu)
        if not put.exists():
            raise FileNotFoundError(f"Файл не найден: {put_k_failu}")
        
        for enc in ['utf-8', 'cp1251']:
            try:
                dframe = pd.read_csv(put, sep=razdelitel, encoding=enc, on_bad_lines='warn')
                if all(col in dframe.columns for col in nuzhnye_stolbtsy):
                    dframe.dropna(subset=['name', 'lat', 'lon'], inplace=True)
                    dframe[['lat', 'lon']] = dframe[['lat', 'lon']].apply(pd.to_numeric, errors='coerce').dropna()
                    if zapros:
                        dframe = dframe.query(zapros)
                    return gpd.GeoDataFrame(dframe, geometry=gpd.points_from_xy(dframe.lon, dframe.lat), crs="EPSG:4326")
            except Exception:
                continue
        raise ValueError(f"Не удалось прочитать '{put_k_failu}' или найти столбцы: {nuzhnye_stolbtsy}.")

    def _zagruzit_csv(self) -> Tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
        print("\nЗагрузка данных об учреждениях из CSV...")
        shkoly = self._prochitat_csv("Rus_schools_final.csv", ",", ['name', 'lat', 'lon', 'addr'], "addr.str.contains('Новокузнецк', na=False)")
        udo = self._prochitat_csv("УДО.csv", ";", ['name', 'lat', 'lon', 'addr'])
        print(f"-> Загружено {len(shkoly)} школ и {len(udo)} УДО.")
        return shkoly, udo
        
    def _obrabotat_dannyie(self):
        print("\nОбработка данных...")
        if self.rayony_gdf.empty:
            print("ПРЕДУПРЕЖДЕНИЕ: Нет данных о районах, обработка невозможна.")
            self.statistika_df = pd.DataFrame()
            return

        nastroiki_soedineniya = {'how': "inner", 'predicate': 'within'}
        self.shkoly_gdf = gpd.sjoin(self.shkoly_gdf, self.rayony_gdf, **nastroiki_soedineniya).rename(columns={'name_left': 'name', 'name_right': 'district_name'})
        self.udo_gdf = gpd.sjoin(self.udo_gdf, self.rayony_gdf, **nastroiki_soedineniya).rename(columns={'name_left': 'name', 'name_right': 'district_name'})
        
        statistika = self.rayony_gdf.set_index('name').copy()
        statistika['school_count'] = self.shkoly_gdf.groupby('district_name').size()
        statistika['udo_count'] = self.udo_gdf.groupby('district_name').size()
        statistika.fillna(0, inplace=True)
        statistika['udo_per_10k_capita'] = (statistika['udo_count'] * 10000 / statistika['population']).round(2)
        self.statistika_df = statistika.reset_index()

        if not self.udo_gdf.empty:
            self.udo_gdf['nearby_schools'] = self.udo_gdf.apply(
                lambda row: '<br>'.join([s['name'] for _, s in self.shkoly_gdf.iterrows()
                    if geodesic((row.geometry.y, row.geometry.x), (s.geometry.y, s.geometry.x)).km <= 3
                ]) or 'Нет школ в радиусе 3 км', axis=1
            )
        print("-> Обработка завершена.")
        
    def _sdelat_statistiku_html(self) -> str:
        if self.statistika_df.empty:
            return "<span class='close-btn'>&times;</span><h4>Статистика недоступна</h4>"

        stroki = "".join([f"<tr><td>{row['name'].replace(' район', '')}</td><td>{int(row['school_count'])}</td><td>{int(row['udo_count'])}</td></tr>"
                        for _, row in self.statistika_df.iterrows()])
        tablica_statistiky = f"<h4>Статистика по районам</h4><table><tr><th>Район</th><th>Школ</th><th>УДО</th></tr>{stroki}</table>"

        sortirovannyi_reiting = self.statistika_df.sort_values('udo_per_10k_capita', ascending=False)
        stroki_reitinga = []
        for i, (_, row) in enumerate(sortirovannyi_reiting.iterrows(), 1):
            rating_class = "rating-1" if i <= 2 else "rating-2" if i <= 4 else "rating-3"
            stroki_reitinga.append(f"<tr class='{rating_class}'><td>{i}</td><td>{row['name'].replace(' район', '')}</td><td>{row['udo_per_10k_capita']}</td></tr>")
        
        tablica_reitinga = f"<br><h4>Рейтинг доступности доп. образования</h4><table><tr><th>#</th><th>Район</th><th>УДО на 10 тыс. чел.</th></tr>{''.join(stroki_reitinga)}</table>"
        return f"<span class='close-btn'>&times;</span>{tablica_statistiky}{tablica_reitinga}"

    def exportirovat_statistiku_v_txt(self, imya_faila: str = "novokuznetsk_statistics.txt"):
        if self.statistika_df.empty:
            print("Нет данных для экспорта статистики.")
            return
        
        zagolovok = "Статистика по районам Новокузнецка\n(Границы: OSM, Учреждения: CSV, Карта: Яндекс)\n" + "="*75 + "\n\n"
        with open(imya_faila, 'w', encoding='utf-8') as f:
            f.write(zagolovok)
            for _, row in self.statistika_df.iterrows():
                f.write(
                    f"Район: {row['name']}\n"
                    f"  - Население (оценочное): {int(row['population'])} чел.\n"
                    f"  - Площадь: {row['area_km2']:.2f} км²\n"
                    f"  - Плотность населения: {row['pop_density']:.2f} чел./км²\n"
                    f"  - Количество школ: {int(row['school_count'])}\n"
                    f"  - Количество УДО: {int(row['udo_count'])}\n"
                    f"  - УДО на 10 тыс. населения: {row['udo_per_10k_capita']}\n\n"
                )
        print(f"Статистика успешно экспортирована в файл '{imya_faila}'.")

    def sgenerirovat_kartu(self, imya_faila: str = "yandex_map.html"):
        # готовим еду для джаваскрипта
        obekty_rayonov = [] if self.rayony_gdf.empty else json.loads(self.rayony_gdf.to_json())['features']
        plotnosti = [f['properties']['pop_density'] for f in obekty_rayonov if f['properties'].get('pop_density')]
        min_p, max_p = (min(plotnosti), max(plotnosti)) if plotnosti else (0, 1)

        def pomenyat_koordinaty(koordinaty):
            if isinstance(koordinaty, (list, tuple)) and len(koordinaty) == 2 and isinstance(koordinaty[0], (int, float)):
                return [koordinaty[1], koordinaty[0]]
            if isinstance(koordinaty, (list, tuple)):
                return [pomenyat_koordinaty(c) for c in koordinaty]
            return koordinaty

        for i, obekt in enumerate(obekty_rayonov):
            obekt['geometry']['coordinates'] = pomenyat_koordinaty(obekt['geometry']['coordinates'])
            plotnost = obekt['properties'].get('pop_density', 0)
            znamenatel = max_p - min_p
            rang = 0 if znamenatel < 1e-6 else int(((plotnost - min_p) / znamenatel) * (len(CHOROPLETH_COLORS) - 1))
            obekt.update({
                'id': f"poly_{i}",
                'properties': {**obekt['properties'], 'fill': CHOROPLETH_COLORS[rang], 'hintContent': f"{obekt['properties']['name']}<br>Плотность: {plotnost} чел./км²"}
            })

        def v_geojson_obekty(geodframe, prefiks):
            features = []
            for i, row in geodframe.iterrows():
                svoistva = {
                    "iconCaption": row["name"], 
                    "balloonContentHeader": row["name"], 
                    "balloonContentBody": f"<b>Адрес:</b> {row.get('addr', 'N/A')}" + (f"<hr><b>Школы в радиусе 3 км:</b><br>{row['nearby_schools']}" if 'nearby_schools' in row else ""),
                    "balloonContentFooter": f'<a href="https://yandex.ru/maps/?rtext=~{row.geometry.y},{row.geometry.x}" target="_blank">Проложить маршрут</a>'
                }
                features.append({"type": "Feature", "id": f"{prefiks}_{i}", "geometry": {"type": "Point", "coordinates": [row.geometry.y, row.geometry.x]}, "properties": svoistva})
            return features

        obekty_shkol = v_geojson_obekty(self.shkoly_gdf, "school")
        obekty_udo = v_geojson_obekty(self.udo_gdf, "udo")
        urovni_legendy = [round(min_p + i * (max_p - min_p) / max(1, len(CHOROPLETH_COLORS)-1)) for i in range(len(CHOROPLETH_COLORS))]
        
        # страшный html в одной строке ведь яндекс api балует)
        shablon = Template(self.HTML_SHABLON)
        gotovyi_html = shablon.render(
            api_key=self.api_klyuch, center_coords=self.centr_karty, fill_opacity=self.prozrachnost,
            districts_features=json.dumps(obekty_rayonov), schools_features=json.dumps(obekty_shkol),
            udo_features=json.dumps(obekty_udo), stats_html=self.statistika_html,
            legend_grades=json.dumps(urovni_legendy), choropleth_colors=json.dumps(CHOROPLETH_COLORS)
        )
        
        Path(imya_faila).write_text(gotovyi_html, encoding='utf-8')
        print(f"\nКарта '{imya_faila}' успешно создана.")

    HTML_SHABLON = """<!DOCTYPE html><html><head><title>Аналитическая карта Новокузнецка</title><meta http-equiv="Content-Type" content="text/html; charset=utf-8" /><script src="https://api-maps.yandex.ru/2.1/?apikey={{ api_key }}&lang=ru_RU" type="text/javascript"></script><style>html, body, #map { width: 100%; height: 100%; padding: 0; margin: 0; font-family: Arial, sans-serif; } .panel { position: absolute; background: rgba(255, 255, 255, 0.95); padding: 12px; border-radius: 6px; box-shadow: 0 2px 6px rgba(0,0,0,0.2); z-index: 1000; } #stats-panel { top: 10px; right: 10px; width: 340px; max-height: calc(100vh - 20px); overflow-y: auto; display: none; } #stats-panel h4 { margin: 0 0 10px 0; text-align: center; } #stats-panel table { width: 100%; border-collapse: collapse; } #stats-panel th, #stats-panel td { border: 1px solid #ccc; padding: 6px; text-align: center; font-size: 13px; } #stats-panel th { background-color: #f2f2f2; } .rating-1 { background-color: #d4edda; } .rating-2 { background-color: #fff3cd; } .rating-3 { background-color: #f8d7da; } .close-btn { position: absolute; top: 5px; right: 10px; font-size: 24px; font-weight: bold; cursor: pointer; color: #aaa; } .close-btn:hover { color: #000; } #legend { bottom: 30px; left: 10px; padding: 8px 10px; font-size: 12px; display: none; } #legend h4 { margin: 0 0 5px 0; text-align: center; } .legend-grade { display: flex; align-items: center; margin-bottom: 2px; } .legend-color { width: 18px; height: 18px; margin-right: 8px; border: 1px solid #777; } </style></head><body><div id="map"></div><div id="stats-panel" class="panel">{{ stats_html | safe }}</div><div id="legend" class="panel"></div><script type="text/javascript">ymaps.ready(init); function init() { const myMap = new ymaps.Map("map", { center: {{ center_coords }}, zoom: 11, controls: ['zoomControl', 'fullscreenControl', 'typeSelector'] }); const districtsData = {{ districts_features | safe }}; const schoolsData = {{ schools_features | safe }}; const udoData = {{ udo_features | safe }}; let activeCircle = null; const districtCollection = new ymaps.GeoObjectCollection(null, {}); districtsData.forEach(feature => { const featureOptions = { fillColor: feature.properties.fill, strokeColor: '#00008B', strokeWidth: 2, strokeOpacity: 0.7, fillOpacity: 0 }; const featureData = { hintContent: feature.properties.hintContent }; if (feature.geometry.type === 'Polygon') { districtCollection.add(new ymaps.Polygon(feature.geometry.coordinates, featureData, featureOptions)); } else if (feature.geometry.type === 'MultiPolygon') { feature.geometry.coordinates.forEach(coords => districtCollection.add(new ymaps.Polygon(coords, featureData, featureOptions))); } }); myMap.geoObjects.add(districtCollection); const legend = document.getElementById('legend'); const legendGrades = {{ legend_grades | safe }}; const densityColors = {{ choropleth_colors | safe }}; legend.innerHTML = "<h4>Плотность, чел./км²</h4>"; for (let i = 0; i < legendGrades.length; i++) { const div = document.createElement('div'); div.className = 'legend-grade'; const nextGrade = legendGrades[i + 1] ? ` &ndash; ${legendGrades[i + 1] - 1}` : '+'; div.innerHTML = `<span class="legend-color" style="background:${densityColors[i]}"></span> ${legendGrades[i]}${nextGrade}`; legend.appendChild(div); } const objectManager = new ymaps.ObjectManager({ clusterize: true, gridSize: 64 }); objectManager.add(JSON.stringify({ type: 'FeatureCollection', features: schoolsData.concat(udoData) })); schoolsData.forEach(f => objectManager.objects.setObjectOptions(f.id, { preset: 'islands#greenEducationIcon' })); udoData.forEach(f => objectManager.objects.setObjectOptions(f.id, { preset: 'islands#blueStarIcon' })); myMap.geoObjects.add(objectManager); objectManager.objects.events.add('click', e => { if (activeCircle) myMap.geoObjects.remove(activeCircle); const obj = objectManager.objects.getById(e.get('objectId')); if (obj.id.startsWith('udo_')) { activeCircle = new ymaps.Circle([obj.geometry.coordinates, 3000], {}, { fillColor: "#888888", fillOpacity: 0.4, strokeColor: "#555555", strokeOpacity: 0.7, strokeWidth: 2 }); myMap.geoObjects.add(activeCircle); } }); objectManager.objects.balloon.events.add('close', () => { if (activeCircle) { myMap.geoObjects.remove(activeCircle); activeCircle = null; } }); const statsPanel = document.getElementById('stats-panel'); const StatsButton = new ymaps.control.Button({ data: { content: '📊', title: 'Показать статистику' }, options: { selectOnClick: true } }); StatsButton.events.add('select', () => statsPanel.style.display = 'block'); StatsButton.events.add('deselect', () => statsPanel.style.display = 'none'); document.querySelector('.close-btn').onclick = () => StatsButton.deselect(); myMap.controls.add(StatsButton, { float: 'right' }); const FillButton = new ymaps.control.Button({ data: { content: '🎨', title: 'Включить/отключить заливку' }, options: { selectOnClick: true } }); FillButton.events.add('select', () => { districtCollection.each(obj => obj.options.set('fillOpacity', {{ fill_opacity }})); legend.style.display = 'block'; }); FillButton.events.add('deselect', () => { districtCollection.each(obj => obj.options.set('fillOpacity', 0)); legend.style.display = 'none'; }); myMap.controls.add(FillButton, { float: 'right' }); } </script></body></html>"""

def poluchit_vvod_polzovatelya() -> Tuple[str, float]:
    # допрос пользователя
    api_klyuch = input("Введите ваш API-ключ от Яндекс.Карт: ")
    while not api_klyuch:
        print("API-ключ не может быть пустым.")
        api_klyuch = input("Введите ваш API-ключ от Яндекс.Карт: ")

    prozrachnost = 0.65
    while True:
        vvod_prozrachnosti = input("Введите процент непрозрачности заливки (от 0 до 100, по умолч. 65): ")
        if not vvod_prozrachnosti:
            break
        try:
            znachenie = int(vvod_prozrachnosti)
            if 0 <= znachenie <= 100:
                prozrachnost = znachenie / 100.0
                break
            print("Ошибка: Введите число от 0 до 100.")
        except ValueError:
            print("Ошибка: Ввод должен быть целым числом.")
    return api_klyuch, prozrachnost

def glavnaya():
    # поехали!
    try:
        api_klyuch, prozrachnost = poluchit_vvod_polzovatelya()
        generator = GeneratorKarty(
            nazvanie_goroda="Новокузнецк", 
            centr_karty=[53.76, 87.15],
            api_klyuch=api_klyuch,
            prozrachnost=prozrachnost
        )
        generator.exportirovat_statistiku_v_txt()
        generator.sgenerirovat_kartu()
    except Exception as e:
        print(f"\nПроизошла критическая ошибка: {e}")

if __name__ == "__main__":
    glavnaya()

