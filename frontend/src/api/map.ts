import { request } from "./client";

export const mapApi = {
  getCountries(date?: string): Promise<any> {
    const params = date ? `?date=${date}` : "";
    return request(`/api/v1/map/countries${params}`);
  },
};
