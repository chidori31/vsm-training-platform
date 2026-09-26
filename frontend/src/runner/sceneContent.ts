export type Portrait = "pair" | "passenger" | "colleague";

export function scenarioTitle(id: string, version: number, title: string) {
  const titles: Record<string, string> = {
    "demo-passenger-conflict": "Конфликт пассажиров",
    "demo-medical-incident": "Медицинская ситуация",
    "demo-service-situation": "Сервисная ситуация",
  };
  return (
    (version === 1 && titles[id]) ||
    title.replace(/^Синтетическое демо:\s*/, "")
  );
}

interface SceneCopy {
  title: string;
  role: string;
  dialogue: string;
  context: string;
  portrait: Portrait;
}

const scenarios: Record<
  string,
  { category: string; context: string; duration: string; portrait: Portrait }
> = {
  "demo-passenger-conflict": {
    category: "01 / КОММУНИКАЦИЯ",
    context:
      "Два пассажира. Разные ожидания. Найдите решение, которое поможет продолжить поездку спокойно.",
    duration: "30 секунд на первое решение",
    portrait: "pair",
  },
  "demo-medical-incident": {
    category: "02 / КООРДИНАЦИЯ",
    context:
      "Пассажир просит о помощи. Ваша задача — вовремя передать запрос и согласовать дальнейшие действия.",
    duration: "20 секунд на первое решение",
    portrait: "passenger",
  },
  "demo-service-situation": {
    category: "03 / СЕРВИС",
    context:
      "Ожидаемая услуга недоступна. Сохраните доверие в разговоре о возможных альтернативах.",
    duration: "40 секунд на первое решение",
    portrait: "passenger",
  },
};

const scenes: Record<string, Record<string, SceneCopy>> = {
  "demo-passenger-conflict": {
    dispute: {
      title: "Два взгляда на одну поездку",
      role: "Пассажир · соседнее место",
      dialogue: "Я рассчитывал на спокойную поездку. Можно сделать потише?",
      context:
        "В вагоне начинается спор из-за шума. Оба пассажира ждут вашей реакции.",
      portrait: "pair",
    },
    options: {
      title: "Найти общий язык",
      role: "Пассажир · разговор продолжается",
      dialogue: "Хорошо, давайте найдём решение, которое устроит нас обоих.",
      context:
        "Вы выслушали обе стороны. Теперь важно договориться о следующем шаге.",
      portrait: "pair",
    },
  },
  "demo-medical-incident": {
    notice: {
      title: "Вовремя услышать",
      role: "Пассажир · запрос помощи",
      dialogue: "Мне нехорошо. Поможете связаться с ответственным сотрудником?",
      context:
        "Пассажир сообщает о плохом самочувствии. Это учебная модель коммуникации, а не медицинская инструкция.",
      portrait: "passenger",
    },
    handover: {
      title: "Передать и подтвердить",
      role: "Коллега · рабочая связь",
      dialogue: "Запрос передан. Давайте уточним дальнейшие действия.",
      context:
        "Вы сообщили о запросе помощи. Продолжите координацию с коллегой.",
      portrait: "colleague",
    },
  },
  "demo-service-situation": {
    request: {
      title: "Когда ожидания выше возможностей",
      role: "Пассажир · сервисный запрос",
      dialogue: "Я рассчитывал на эту услугу. Что вы можете предложить взамен?",
      context:
        "Запрошенная услуга недоступна в этой поездке. Пассажиру нужно понятное решение.",
      portrait: "passenger",
    },
    alternative: {
      title: "Предложить следующий шаг",
      role: "Пассажир · обсуждение альтернатив",
      dialogue: "Понимаю ограничение. Давайте обсудим, что сейчас возможно.",
      context:
        "Причина ограничения понятна. Согласуйте доступный вариант с пассажиром.",
      portrait: "passenger",
    },
  },
};

export function scenarioPresentation(id: string, version: number) {
  return (
    (version === 1 && scenarios[id]) || {
      category: "УЧЕБНАЯ СИТУАЦИЯ",
      context:
        "Прочитайте ситуацию и выберите действие. Последствия зависят от ваших решений.",
      duration: "Время указано в каждой сцене",
      portrait: "passenger" as Portrait,
    }
  );
}

export function scenePresentation(
  id: string,
  version: number,
  node: string,
  text: string,
) {
  const copy = version === 1 ? scenes[id]?.[node] : undefined;
  return {
    ...(copy ?? {
      title: "Рабочая ситуация",
      role: "Контекст сценария",
      dialogue: text,
      context: "Изучите ситуацию перед выбором действия.",
      portrait: "passenger" as Portrait,
    }),
    isDialogue: Boolean(copy),
  };
}
