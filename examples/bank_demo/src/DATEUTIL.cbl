       IDENTIFICATION DIVISION.
       PROGRAM-ID. DATEUTIL.
      *================================================================*
      * DATEUTIL - CALENDAR SERVICES FOR THE BANK BATCH SUITE          *
      * DAY NUMBERS COUNT FROM 1900-01-01 (= DAY 1, A MONDAY).         *
      * VALID RANGE IS 1900-01-01 THROUGH 2099-12-31.                  *
      * NOTE: OUTPUT FIELDS ARE LEFT UNTOUCHED WHEN A DATE IS INVALID. *
      *================================================================*
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-CUM-DAYS-TABLE.
           05 FILLER               PIC X(36) VALUE
              '000031059090120151181212243273304334'.
       01  WS-CUM-DAYS REDEFINES WS-CUM-DAYS-TABLE.
           05 WS-CUM               PIC 9(3) OCCURS 12 TIMES.
       01  WS-MONTH-LEN-TABLE.
           05 FILLER               PIC X(24) VALUE
              '312831303130313130313031'.
       01  WS-MONTH-LENS REDEFINES WS-MONTH-LEN-TABLE.
           05 WS-MLEN              PIC 99 OCCURS 12 TIMES.
       01  WS-WORK.
           05 WS-DATE.
              10 WS-YYYY           PIC 9(4).
              10 WS-MM             PIC 99.
              10 WS-DD             PIC 99.
           05 WS-DATE-N REDEFINES WS-DATE
                                   PIC 9(8).
           05 WS-LEAP              PIC X.
           05 WS-VALID             PIC X.
           05 WS-DAYNUM            PIC S9(7) COMP-3.
           05 WS-DAYNUM-1          PIC S9(7) COMP-3.
           05 WS-MAX-DAY           PIC 99.
           05 WS-Y                 PIC 9(4).
           05 WS-Q                 PIC 9(4).
           05 WS-R                 PIC 9(4).
           05 WS-LEAPS             PIC 9(4).
           05 WS-YEAR-LEN          PIC 9(3).
           05 WS-MONTH-LEN         PIC 99.
           05 WS-REMAIN            PIC S9(7) COMP-3.
       LINKAGE SECTION.
       COPY DATEPARM.
       PROCEDURE DIVISION USING DATE-PARMS.
       0000-MAIN.
           MOVE 0 TO DP-RETURN-CODE
           EVALUATE TRUE
               WHEN DP-VALIDATE
                   MOVE DP-DATE-1 TO WS-DATE-N
                   PERFORM 1000-CHECK-DATE
                   IF WS-VALID = 'N'
                       MOVE 10 TO DP-RETURN-CODE
                   END-IF
               WHEN DP-DAYS-BETWEEN
                   MOVE DP-DATE-1 TO WS-DATE-N
                   PERFORM 1000-CHECK-DATE
                   IF WS-VALID = 'Y'
                       PERFORM 2000-DATE-TO-DAYNUM
                       MOVE WS-DAYNUM TO WS-DAYNUM-1
                       MOVE DP-DATE-2 TO WS-DATE-N
                       PERFORM 1000-CHECK-DATE
                   END-IF
                   IF WS-VALID = 'Y'
                       PERFORM 2000-DATE-TO-DAYNUM
                       COMPUTE DP-DAYS = WS-DAYNUM - WS-DAYNUM-1
                   ELSE
                       MOVE 10 TO DP-RETURN-CODE
                   END-IF
               WHEN DP-ADD-DAYS
                   MOVE DP-DATE-1 TO WS-DATE-N
                   PERFORM 1000-CHECK-DATE
                   IF WS-VALID = 'N'
                       MOVE 10 TO DP-RETURN-CODE
                   ELSE
                       PERFORM 2000-DATE-TO-DAYNUM
                       ADD DP-DAYS TO WS-DAYNUM
                       IF WS-DAYNUM < 1 OR WS-DAYNUM > 73049
                           MOVE 20 TO DP-RETURN-CODE
                       ELSE
                           PERFORM 3000-DAYNUM-TO-DATE
                           MOVE WS-DATE-N TO DP-RESULT-DATE
                       END-IF
                   END-IF
               WHEN DP-DAY-OF-WEEK
                   MOVE DP-DATE-1 TO WS-DATE-N
                   PERFORM 1000-CHECK-DATE
                   IF WS-VALID = 'N'
                       MOVE 10 TO DP-RETURN-CODE
                   ELSE
                       PERFORM 2000-DATE-TO-DAYNUM
                       SUBTRACT 1 FROM WS-DAYNUM
                       DIVIDE WS-DAYNUM BY 7 GIVING WS-Q
                           REMAINDER WS-R
                       COMPUTE DP-DOW = WS-R + 1
                   END-IF
               WHEN OTHER
                   MOVE 99 TO DP-RETURN-CODE
           END-EVALUATE
           GOBACK.

      *----------------------------------------------------------------*
      * SETS WS-VALID AND WS-LEAP FOR THE DATE IN WS-DATE              *
      *----------------------------------------------------------------*
       1000-CHECK-DATE.
           MOVE 'Y' TO WS-VALID
           IF WS-YYYY < 1900 OR WS-YYYY > 2099
               MOVE 'N' TO WS-VALID
           END-IF
           IF WS-MM < 1 OR WS-MM > 12
               MOVE 'N' TO WS-VALID
           END-IF
           IF WS-VALID = 'Y'
               MOVE WS-YYYY TO WS-Y
               PERFORM 1100-LEAP-YEAR
               MOVE WS-MLEN(WS-MM) TO WS-MAX-DAY
               IF WS-MM = 2 AND WS-LEAP = 'Y'
                   ADD 1 TO WS-MAX-DAY
               END-IF
               IF WS-DD < 1 OR WS-DD > WS-MAX-DAY
                   MOVE 'N' TO WS-VALID
               END-IF
           END-IF.

       1100-LEAP-YEAR.
           MOVE 'N' TO WS-LEAP
           DIVIDE WS-Y BY 4 GIVING WS-Q REMAINDER WS-R
           IF WS-R = 0
               MOVE 'Y' TO WS-LEAP
               DIVIDE WS-Y BY 100 GIVING WS-Q REMAINDER WS-R
               IF WS-R = 0
                   MOVE 'N' TO WS-LEAP
                   DIVIDE WS-Y BY 400 GIVING WS-Q REMAINDER WS-R
                   IF WS-R = 0
                       MOVE 'Y' TO WS-LEAP
                   END-IF
               END-IF
           END-IF.

      *----------------------------------------------------------------*
      * WS-DATE (VALID) -> WS-DAYNUM                                   *
      *----------------------------------------------------------------*
       2000-DATE-TO-DAYNUM.
           COMPUTE WS-Y = WS-YYYY - 1
           DIVIDE WS-Y BY 4 GIVING WS-LEAPS
           DIVIDE WS-Y BY 100 GIVING WS-Q
           SUBTRACT WS-Q FROM WS-LEAPS
           DIVIDE WS-Y BY 400 GIVING WS-Q
           ADD WS-Q TO WS-LEAPS
           SUBTRACT 460 FROM WS-LEAPS
           COMPUTE WS-DAYNUM = (WS-YYYY - 1900) * 365 + WS-LEAPS
                             + WS-CUM(WS-MM) + WS-DD
           IF WS-MM > 2 AND WS-LEAP = 'Y'
               ADD 1 TO WS-DAYNUM
           END-IF.

      *----------------------------------------------------------------*
      * WS-DAYNUM (1..73049) -> WS-DATE                                *
      *----------------------------------------------------------------*
       3000-DAYNUM-TO-DATE.
           MOVE WS-DAYNUM TO WS-REMAIN
           MOVE 1900 TO WS-Y
           PERFORM 1100-LEAP-YEAR
           PERFORM 3100-SET-YEAR-LEN
           PERFORM UNTIL WS-REMAIN <= WS-YEAR-LEN
               SUBTRACT WS-YEAR-LEN FROM WS-REMAIN
               ADD 1 TO WS-Y
               PERFORM 1100-LEAP-YEAR
               PERFORM 3100-SET-YEAR-LEN
           END-PERFORM
           MOVE WS-Y TO WS-YYYY
           MOVE 1 TO WS-MM
           PERFORM 3200-SET-MONTH-LEN
           PERFORM UNTIL WS-REMAIN <= WS-MONTH-LEN
               SUBTRACT WS-MONTH-LEN FROM WS-REMAIN
               ADD 1 TO WS-MM
               PERFORM 3200-SET-MONTH-LEN
           END-PERFORM
           MOVE WS-REMAIN TO WS-DD.

       3100-SET-YEAR-LEN.
           IF WS-LEAP = 'Y'
               MOVE 366 TO WS-YEAR-LEN
           ELSE
               MOVE 365 TO WS-YEAR-LEN
           END-IF.

       3200-SET-MONTH-LEN.
           MOVE WS-MLEN(WS-MM) TO WS-MONTH-LEN
           IF WS-MM = 2 AND WS-LEAP = 'Y'
               ADD 1 TO WS-MONTH-LEN
           END-IF.
