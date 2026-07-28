import { request } from "./client";

export interface MapCountryItem {
  country_code: string;
  country_name_zh: string;
  article_count_today: number;
  top_topics: { topic_id: string; name: string; salience_score: number; article_count: number }[];
  coverage_confidence: number;
  degraded: boolean;
  data_delay_minutes: number;
}

export interface MapCountriesData {
  items: MapCountryItem[];
  data_delay_minutes: number;
  coverage_confidence: number;
}

export const mapApi = {
  getCountries(date?: string): Promise<MapCountriesData> {
    const params = date ? `?date=${date}` : "";
    return request(`/api/v1/map/countries${params}`);
  },
};
