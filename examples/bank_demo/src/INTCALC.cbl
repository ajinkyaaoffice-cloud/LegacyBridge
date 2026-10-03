       IDENTIFICATION DIVISION.
       PROGRAM-ID. INTCALC.
      *================================================================*
      * INTCALC - INTEREST ACCRUAL BETWEEN TWO DATES                   *
      *   RC 00 OK, 10 BAD DATE, 20 END BEFORE START,                  *
      *      30 COMPOUNDING PERIOD TOO LONG, 40 OVERFLOW, 99 BAD METHOD*
      * METHODS M AND C STORE WITHOUT SIZE CHECKING (TRUNCATION).      *
      *================================================================*
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       COPY DATEPARM.
       01  WS-D1.
           05 WS-D1-YYYY           PIC 9(4).
           05 WS-D1-MM             PIC 99.
           05 WS-D1-DD             PIC 99.
       01  WS-D1-N REDEFINES WS-D1 PIC 9(8).
       01  WS-D2.
           05 WS-D2-YYYY           PIC 9(4).
           05 WS-D2-MM             PIC 99.
           05 WS-D2-DD             PIC 99.
       01  WS-D2-N REDEFINES WS-D2 PIC 9(8).
       01  WS-DAY1                 PIC 99.
       01  WS-DAY2                 PIC 99.
       01  WS-DAYS360              PIC S9(7) COMP-3.
       01  WS-CBAL                 PIC S9(11)V9(6) COMP-3.
       01  WS-I                    PIC 9(5) COMP-3.
       LINKAGE SECTION.
       COPY INTPARM.
       PROCEDURE DIVISION USING INT-PARMS.
       0000-MAIN.
           MOVE 0 TO IP-RETURN-CODE
           MOVE 0 TO IP-INTEREST
           MOVE 'V' TO DP-FUNCTION
           MOVE IP-START-DATE TO DP-DATE-1
           CALL 'DATEUTIL' USING DATE-PARMS
           IF DP-RETURN-CODE NOT = 0
               MOVE 10 TO IP-RETURN-CODE
               GOBACK
           END-IF
           MOVE IP-END-DATE TO DP-DATE-1
           CALL 'DATEUTIL' USING DATE-PARMS
           IF DP-RETURN-CODE NOT = 0
               MOVE 10 TO IP-RETURN-CODE
               GOBACK
           END-IF
           EVALUATE IP-METHOD
               WHEN 'S'
                   PERFORM 1000-ACTUAL-DAYS
                   IF IP-RETURN-CODE = 0
                       COMPUTE IP-INTEREST ROUNDED = IP-PRINCIPAL
                           * IP-ANNUAL-RATE * IP-DAYS / 365
                           ON SIZE ERROR
                               MOVE 40 TO IP-RETURN-CODE
                               MOVE 0 TO IP-INTEREST
                       END-COMPUTE
                   END-IF
               WHEN 'M'
                   PERFORM 2000-DAYS-360
                   IF IP-RETURN-CODE = 0
                       COMPUTE IP-INTEREST = IP-PRINCIPAL
                           * IP-ANNUAL-RATE * WS-DAYS360 / 360
                   END-IF
               WHEN 'C'
                   PERFORM 1000-ACTUAL-DAYS
                   IF IP-RETURN-CODE = 0 AND IP-DAYS > 3660
                       MOVE 30 TO IP-RETURN-CODE
                   END-IF
                   IF IP-RETURN-CODE = 0
                       PERFORM 3000-COMPOUND-DAILY
                   END-IF
               WHEN OTHER
                   MOVE 99 TO IP-RETURN-CODE
           END-EVALUATE
           GOBACK.

       1000-ACTUAL-DAYS.
           MOVE 'B' TO DP-FUNCTION
           MOVE IP-START-DATE TO DP-DATE-1
           MOVE IP-END-DATE TO DP-DATE-2
           CALL 'DATEUTIL' USING DATE-PARMS
           MOVE DP-DAYS TO IP-DAYS
           IF IP-DAYS < 0
               MOVE 20 TO IP-RETURN-CODE
           END-IF.

      *----------------------------------------------------------------*
      * US 30/360: DAY 31 COUNTS AS 30; END DAY 31 COUNTS AS 30 ONLY   *
      * WHEN THE START DAY IS 30 OR 31.                                *
      *----------------------------------------------------------------*
       2000-DAYS-360.
           MOVE IP-START-DATE TO WS-D1-N
           MOVE IP-END-DATE TO WS-D2-N
           MOVE WS-D1-DD TO WS-DAY1
           IF WS-DAY1 = 31
               MOVE 30 TO WS-DAY1
           END-IF
           MOVE WS-D2-DD TO WS-DAY2
           IF WS-DAY2 = 31 AND WS-DAY1 = 30
               MOVE 30 TO WS-DAY2
           END-IF
           COMPUTE WS-DAYS360 = (WS-D2-YYYY - WS-D1-YYYY) * 360
                              + (WS-D2-MM - WS-D1-MM) * 30
                              + (WS-DAY2 - WS-DAY1)
           IF WS-DAYS360 < 0
               MOVE 20 TO IP-RETURN-CODE
           ELSE
               MOVE WS-DAYS360 TO IP-DAYS
           END-IF.

       3000-COMPOUND-DAILY.
           MOVE IP-PRINCIPAL TO WS-CBAL
           PERFORM VARYING WS-I FROM 1 BY 1 UNTIL WS-I > IP-DAYS
               COMPUTE WS-CBAL = WS-CBAL
                   + WS-CBAL * IP-ANNUAL-RATE / 365
           END-PERFORM
           COMPUTE IP-INTEREST ROUNDED = WS-CBAL - IP-PRINCIPAL.
